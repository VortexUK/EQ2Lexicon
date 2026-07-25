"""Server statistics — the census character.stat aggregate family surfaced
as a per-server Stats page + per-character lifetime panel.

Data shape (probed 2026-07-25):
  - character.stat.global: one row per classid + 'all', game-wide.
  - character.stat.world:  one row per worldid.
  - character.stat:        one row per 'worldid.classid'.
  Each row: 12 lifetime statistics as {max, sum, avg} + count + ts,
  recomputed daily by census (~09:45 UTC).

Named leaderboards come from sorted/filtered character queries:
  - single-value stats sort server-side ('statistics.kills:-1'),
  - K/D sorts on '...ratio.value' with a kills floor (pure ratio farming
    from a handful of kills would otherwise own the board),
  - the two hit records can't be census-sorted (two-key objects), so we
    range-filter near the aggregate max and sort client-side.

Everything is cached per world for STATS_TTL_S (census only recomputes
daily) with stale-while-revalidate semantics, so page views cost zero
census once warm.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.core.log_safety import scrub
from backend.eq2db.classes import catalogue as classes_db
from backend.eq2db.spells import catalogue as spells_db
from backend.server.auth_deps import require_user_session as _require_user
from backend.server.cache import lifetime_cache
from backend.server.core.census_lifecycle import shared_census_client
from backend.server.core.validation import validate_character_name
from backend.server.limiter import limiter
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["stats"])

STATS_TTL_S = 6 * 3600  # census recomputes daily; refresh at most every 6 h
LEADER_LIMIT = 10
LEADER_BUILD_DEADLINE_S = 60  # hard cap on the leaderboard build phase
KD_KILLS_FLOOR = 1000  # K/D board: require this many kills so ratio farming can't own it

# The aggregate stat keys we surface (aggregate-only ones like
# quests.complete can't be resolved to named characters — census rejects
# per-character sorts on them — so they appear in totals/averages only).
_TOTAL_STATS = ("kills", "deaths", "quests.complete", "collections.complete", "items_crafted", "rare_harvests")
_AVG_STATS = ("kills", "deaths", "kills_deaths_ratio", "quests.complete", "achievements.points")

# Leaderboards: (key, census sort path, extra filter) — None sort = range mode.
_LEADER_SORTS: list[tuple[str, str | None, dict[str, str] | None]] = [
    ("kills", "statistics.kills", None),
    ("deaths", "statistics.deaths", None),
    ("kills_deaths_ratio", "statistics.kills_deaths_ratio.value", {"statistics.kills.value": f"]{KD_KILLS_FLOOR}"}),
    ("items_crafted", "statistics.items_crafted", None),
    ("rare_harvests", "statistics.rare_harvests", None),
    ("max_melee_hit", None, None),  # range mode
    ("max_magic_hit", None, None),  # range mode
]


class StatAggregate(BaseModel):
    max: float = 0
    avg: float = 0
    sum: float = 0


class ClassStatsRow(BaseModel):
    classid: int
    name: str
    count: int
    avg: dict[str, float] = {}  # stat key → world avg for this class
    global_avg: dict[str, float] = {}  # same stats, all-worlds average


class LeaderEntry(BaseModel):
    name: str
    cls: str | None = None
    level: int | None = None
    value: float


class ServerStatsResponse(BaseModel):
    world: str
    ts: int  # census aggregate compute time
    fetched_at: int
    population: int
    totals: dict[str, float] = {}
    records: dict[str, float] = {}  # anonymous aggregate maxes
    averages: dict[str, float] = {}
    global_averages: dict[str, float] = {}
    classes: list[ClassStatsRow] = []
    leaders: dict[str, list[LeaderEntry]] = {}


class LifetimeStatResponse(BaseModel):
    character_name: str
    kills: int | None = None
    deaths: int | None = None
    kills_deaths_ratio: float | None = None
    max_melee_hit: int | None = None
    max_melee_ability: str | None = None
    max_magic_hit: int | None = None
    max_magic_ability: str | None = None
    items_crafted: int | None = None
    rare_harvests: int | None = None
    # Context: this world's per-class averages for "vs average" chips.
    class_avg_kills: float | None = None
    class_avg_kd: float | None = None


def _classid_names() -> dict[int, str]:
    """classes.db's icon_id column IS the census classid scheme (Templar=13,
    Mystic=19 — verified against live character type.classid)."""
    return {int(row["icon_id"]): row["name"] for row in classes_db.list_all() if row.get("icon_id") is not None}


def _agg(value: dict, stat: str) -> StatAggregate:
    raw = value.get(stat) or {}
    return StatAggregate(max=raw.get("max") or 0, avg=raw.get("avg") or 0, sum=raw.get("sum") or 0)


def _leader_entries(rows: list[dict], stat: str, limit: int = LEADER_LIMIT) -> list[LeaderEntry]:
    out = []
    for ch in rows:
        st = (ch.get("statistics") or {}).get(stat) or {}
        value = st.get("value")
        if value is None:
            continue
        out.append(
            LeaderEntry(
                name=(ch.get("name") or {}).get("first") or "?",
                cls=(ch.get("type") or {}).get("class"),
                level=(ch.get("type") or {}).get("level"),
                value=float(value),
            )
        )
    out.sort(key=lambda e: -e.value)
    return out[:limit]


async def _build_server_stats(world: str) -> ServerStatsResponse | None:
    """One full refresh: 3 aggregate collections + ~7 leader queries."""
    started = time.monotonic()
    async with shared_census_client() as client:
        worldid = await client.get_worldid(world)
        if worldid is None:
            return None
        aggregates = await client.get_stat_aggregates()
        if aggregates is None:
            return None

        world_row = next((r["value"] for r in aggregates["world"] if str(r.get("id")) == str(worldid)), None)
        if world_row is None:
            return None
        global_all = next((r["value"] for r in aggregates["global"] if r.get("id") == "all"), {})
        global_by_class = {str(r.get("id")): r["value"] for r in aggregates["global"] if r.get("id") != "all"}
        world_by_class = {
            str(r.get("id")).split(".", 1)[1]: r["value"]
            for r in aggregates["world_class"]
            if str(r.get("id")).startswith(f"{worldid}.")
        }

        names = _classid_names()
        classes = []
        for cid_str, value in world_by_class.items():
            try:
                cid = int(cid_str)
            except ValueError:
                continue
            classes.append(
                ClassStatsRow(
                    classid=cid,
                    name=names.get(cid, f"Class {cid}"),
                    count=int(value.get("count") or 0),
                    avg={s: _agg(value, s).avg for s in _AVG_STATS},
                    global_avg={s: _agg(global_by_class.get(cid_str, {}), s).avg for s in _AVG_STATS},
                )
            )
        classes.sort(key=lambda c: -c.count)

        # Leaderboards — sorted queries for single-value stats, range mode for
        # the two-key hit records. Individual failures degrade to an absent
        # board rather than failing the page.
        # Leaderboard queries run sequentially (census drops back-to-back
        # sorted queries when parallelised) but under a HARD deadline: a
        # flaky census must never hang the page — boards that didn't make
        # the cut are simply absent until the next 6-hourly refresh.
        leaders: dict[str, list[LeaderEntry]] = {}
        deadline = time.monotonic() + LEADER_BUILD_DEADLINE_S
        for stat, sort_path, extra in _LEADER_SORTS:
            if time.monotonic() > deadline:
                _log.warning("[stats] leader build deadline hit on %s — %d boards done", world, len(leaders))
                break
            try:
                rows = None
                for attempt in range(2):
                    if attempt:
                        await asyncio.sleep(1.5)
                    if sort_path is not None:
                        rows = await client.get_stat_leaders(world, sort_path, LEADER_LIMIT, extra)
                    else:
                        record = _agg(world_row, stat).max
                        if record <= 0:
                            break
                        # Adaptive floor: near the record first, widening until
                        # the board fills (worlds concentrate at the top).
                        for fraction in (0.5, 0.1, 0.01):
                            rows = await client.get_stat_range(world, f"statistics.{stat}.value", record * fraction)
                            if rows is not None and len(rows) >= LEADER_LIMIT:
                                break
                    if rows or time.monotonic() > deadline:
                        break
                if not rows:
                    _log.warning("[stats] leader query failed for %s on %s", stat, world)
                    continue
                entries = _leader_entries(rows, stat)
                if entries:
                    leaders[stat] = entries
            except Exception as exc:
                _log.warning("[stats] leader board %s failed on %s: %s", stat, world, exc)

    _log.info(
        "[stats] built %s stats in %.1fs (%d classes, %d leader boards)",
        world,
        time.monotonic() - started,
        len(classes),
        len(leaders),
    )
    return ServerStatsResponse(
        world=world,
        ts=int(world_row.get("ts") or 0),
        fetched_at=int(time.time()),
        population=int(world_row.get("count") or 0),
        totals={s: _agg(world_row, s).sum for s in _TOTAL_STATS},
        records={
            s: _agg(world_row, s).max
            for s in (
                "kills",
                "deaths",
                "max_melee_hit",
                "max_magic_hit",
                "quests.complete",
                "items_crafted",
                "rare_harvests",
            )
        },
        averages={s: _agg(world_row, s).avg for s in _AVG_STATS},
        global_averages={s: _agg(global_all, s).avg for s in _AVG_STATS},
        classes=classes,
        leaders=leaders,
    )


# world → (fetched_at_monotonic, payload). Builds take ~1-2 min against a
# slow census (sorted leader queries), so a request NEVER builds inline:
# cold hits kick a background build and return 202; the page polls.
_stats_cache: dict[str, tuple[float, ServerStatsResponse]] = {}
_stats_locks: dict[str, asyncio.Lock] = {}
_last_build_failure: dict[str, float] = {}
_BUILD_FAILURE_COOLDOWN_S = 60


def _lock_for(world: str) -> asyncio.Lock:
    return _stats_locks.setdefault(world, asyncio.Lock())


async def _refresh_server_stats(world: str) -> ServerStatsResponse | None:
    async with _lock_for(world):
        cached = _stats_cache.get(world)
        if cached and time.monotonic() - cached[0] < STATS_TTL_S:
            return cached[1]  # someone else refreshed while we waited
        built = await _build_server_stats(world)
        if built is not None:
            _stats_cache[world] = (time.monotonic(), built)
            _last_build_failure.pop(world, None)
        else:
            _last_build_failure[world] = time.monotonic()
        return built


async def prewarm_server_stats() -> None:
    """Startup task: build every registered server's stats in the background
    (sequentially — census throttles parallel sorted queries) so the first
    page view after boot is a cache hit, not a minute-long build."""
    from backend.server.core.executor import run_sync
    from backend.server.db.servers import store as servers_store

    try:
        servers = await run_sync(servers_store.list_servers_sync)
    except Exception as exc:
        _log.warning("[stats] prewarm skipped — server registry unavailable: %s", exc)
        return
    for srv in servers:
        try:
            await _refresh_server_stats(srv["world"])
        except Exception as exc:
            _log.warning("[stats] prewarm failed for %s: %s", srv.get("world"), exc)


@router.get("/stats/server", response_model=None)
@limiter.limit("30/minute")
async def get_server_stats(request: Request) -> ServerStatsResponse | JSONResponse:
    """Warm cache → the payload (with SWR background refresh when stale).
    Cold → kick a background build and 202; the frontend polls. A recently
    failed build → 503 so the poll loop doesn't spin forever."""
    _require_user(request)
    world = current_world()

    cached = _stats_cache.get(world)
    if cached:
        if time.monotonic() - cached[0] >= STATS_TTL_S:
            asyncio.create_task(_refresh_server_stats(world))
        return cached[1]

    failed_at = _last_build_failure.get(world)
    if failed_at is not None and time.monotonic() - failed_at < _BUILD_FAILURE_COOLDOWN_S:
        raise HTTPException(
            status_code=503, detail="Server statistics unavailable (Census unreachable). Try again shortly."
        )

    asyncio.create_task(_refresh_server_stats(world))  # lock-deduped
    return JSONResponse(status_code=202, content={"status": "building"})


# ---------------------------------------------------------------------------
# Explorer — "show me the highest <stat> <class>s"
# ---------------------------------------------------------------------------
# Whitelisted stat catalogue: key → (census sort path, value extraction path,
# extra filter). Combat stats are point-in-time census snapshots
# (stats.combat.*); lifetime stats live under statistics.*. Class filtering
# uses type.classid (numeric — census silently ignores type.class strings).

_EXPLORE_STATS: dict[str, tuple[str, tuple[str, ...], dict[str, str] | None]] = {
    # Combat snapshot
    "ability_mod": ("stats.combat.abilitymod", ("stats", "combat", "abilitymod"), None),
    "potency": ("stats.combat.basemodifier", ("stats", "combat", "basemodifier"), None),
    "crit_bonus": ("stats.combat.critbonus", ("stats", "combat", "critbonus"), None),
    "crit_chance": ("stats.combat.critchance", ("stats", "combat", "critchance"), None),
    "multi_attack": ("stats.combat.doubleattackchance", ("stats", "combat", "doubleattackchance"), None),
    "dps": ("stats.combat.dps", ("stats", "combat", "dps"), None),
    "attack_speed": ("stats.combat.attackspeed", ("stats", "combat", "attackspeed"), None),
    "flurry": ("stats.combat.flurry", ("stats", "combat", "flurry"), None),
    "block_chance": ("stats.combat.blockchance", ("stats", "combat", "blockchance"), None),
    "strikethrough": ("stats.combat.strikethrough", ("stats", "combat", "strikethrough"), None),
    "max_health": ("stats.health.max", ("stats", "health", "max"), None),
    "max_power": ("stats.power.max", ("stats", "power", "max"), None),
    # Progression (top-level scalar fields — sortable, unlike the statistics.*
    # family's aggregate-only quests/collections/achievements buckets)
    "quests_completed": ("quests.complete", ("quests", "complete"), None),
    "collections_completed": ("collections.complete", ("collections", "complete"), None),
    "achievement_points": ("achievements.points", ("achievements", "points"), None),
    "achievements_completed": ("achievements.completed", ("achievements", "completed"), None),
    "aa_points": ("alternateadvancements.spentpoints", ("alternateadvancements", "spentpoints"), None),
    # Lifetime
    "kills": ("statistics.kills", ("statistics", "kills", "value"), None),
    "deaths": ("statistics.deaths", ("statistics", "deaths", "value"), None),
    "kd_ratio": (
        "statistics.kills_deaths_ratio.value",
        ("statistics", "kills_deaths_ratio", "value"),
        {"statistics.kills.value": f"]{KD_KILLS_FLOOR}"},
    ),
    "items_crafted": ("statistics.items_crafted", ("statistics", "items_crafted", "value"), None),
    "rare_harvests": ("statistics.rare_harvests", ("statistics", "rare_harvests", "value"), None),
}

EXPLORE_LIMIT = 20
_EXPLORE_TTL_S = 900  # combat snapshots move slowly; 15 min keeps it fun-fresh
_explore_cache: dict[str, tuple[float, ExploreResponse]] = {}


class ExploreResponse(BaseModel):
    stat: str
    cls: str | None = None
    entries: list[LeaderEntry] = []


def _classid_for_name(cls: str) -> int | None:
    for cid, name in _classid_names().items():
        if name.lower() == cls.lower():
            return cid
    return None


def _walk(row: dict, path: tuple[str, ...]) -> Any:
    value: Any = row
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


@router.get("/stats/explore", response_model=ExploreResponse)
@limiter.limit("30/minute")
async def explore_stats(request: Request, stat: str, cls: str | None = None) -> ExploreResponse:
    """Live top-N by any whitelisted stat, optionally scoped to one class —
    'show me the highest Ability Mod Templars'."""
    _require_user(request)
    entry = _EXPLORE_STATS.get(stat)
    if entry is None:
        raise HTTPException(status_code=400, detail=f"Unknown stat: {stat}")
    sort_path, value_path, extra = entry

    classid = None
    if cls:
        classid = _classid_for_name(cls)
        if classid is None:
            raise HTTPException(status_code=400, detail=f"Unknown class: {cls}")

    world = current_world()
    cache_key = f"{world.lower()}:{stat}:{classid or 'all'}"
    cached = _explore_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _EXPLORE_TTL_S:
        return cached[1]

    filters = dict(extra or {})
    if classid is not None:
        filters["type.classid"] = str(classid)
    # stats/statistics project the whole subtree (cheap); progression fields
    # project the exact scalar — c:show=achievements would drag the full
    # 500+-item achievement_list along with it.
    show = value_path[0] if value_path[0] in ("stats", "statistics") else ".".join(value_path)
    async with shared_census_client() as client:
        rows = await client.get_stat_leaders(world, sort_path, EXPLORE_LIMIT, filters, show=show)
    if rows is None:
        if cached:
            return cached[1]  # stale beats an error page
        raise HTTPException(status_code=503, detail="Census is not answering — try again shortly.")

    entries = []
    for ch in rows:
        value = _walk(ch, value_path)
        if value is None:
            continue
        entries.append(
            LeaderEntry(
                name=(ch.get("name") or {}).get("first") or "?",
                cls=(ch.get("type") or {}).get("class"),
                level=(ch.get("type") or {}).get("level"),
                value=float(value),
            )
        )
    entries.sort(key=lambda e: -e.value)

    resp = ExploreResponse(stat=stat, cls=cls, entries=entries[:EXPLORE_LIMIT])
    _explore_cache[cache_key] = (time.monotonic(), resp)
    return resp


def _resolve_ability(crc: Any) -> str | None:
    """Best-effort crc → ability name via spells.db (coverage is partial —
    sentinel/unknown crcs simply render unnamed)."""
    try:
        crc_int = int(crc)
    except (TypeError, ValueError):
        return None
    if crc_int <= 0 or crc_int >= 2**32 - 1:  # 4294967295 = census sentinel
        return None
    row = spells_db.find_by_crc(crc_int)
    return row.get("name") if row else None


@router.get("/stats/character/{name}", response_model=LifetimeStatResponse)
@limiter.limit("30/minute")
async def get_character_lifetime(request: Request, name: str) -> LifetimeStatResponse:
    """A character's lifetime statistics + their class's world averages for
    the sheet's "vs average" context."""
    _require_user(request)
    sanitised = validate_character_name(name)
    if sanitised is None:
        raise HTTPException(status_code=400, detail="Character name is invalid.")
    name = sanitised
    world = current_world()
    cache_key = f"lifetime:{name.lower()}:{world.lower()}"

    cached, is_stale = lifetime_cache.get_stale(cache_key)
    if cached is not None and not is_stale:
        return cached

    async with shared_census_client() as client:
        ch = await client.get_character_statistics(name, world)
    if ch is None:
        if cached is not None:
            return cached  # stale beats nothing when census flakes
        raise HTTPException(status_code=404, detail=f"No lifetime statistics for '{name}'.")

    st = ch.get("statistics") or {}
    cls = (ch.get("type") or {}).get("class")

    def _val(stat: str) -> int | None:
        v = (st.get(stat) or {}).get("value")
        return int(v) if v is not None else None

    # Class context from the (SWR-cached) server aggregates — best-effort.
    class_avg_kills = class_avg_kd = None
    server_stats = _stats_cache.get(world, (0, None))[1]
    if server_stats and cls:
        row = next((c for c in server_stats.classes if c.name == cls), None)
        if row:
            class_avg_kills = row.avg.get("kills")
            class_avg_kd = row.avg.get("kills_deaths_ratio")

    kd = (st.get("kills_deaths_ratio") or {}).get("value")
    resp = LifetimeStatResponse(
        character_name=(ch.get("name") or {}).get("first") or name,
        kills=_val("kills"),
        deaths=_val("deaths"),
        kills_deaths_ratio=round(float(kd), 1) if kd is not None else None,
        max_melee_hit=_val("max_melee_hit"),
        max_melee_ability=_resolve_ability((st.get("max_melee_hit") or {}).get("weapon")),
        max_magic_hit=_val("max_magic_hit"),
        max_magic_ability=_resolve_ability((st.get("max_magic_hit") or {}).get("spell")),
        items_crafted=_val("items_crafted"),
        rare_harvests=_val("rare_harvests"),
        class_avg_kills=class_avg_kills,
        class_avg_kd=class_avg_kd,
    )
    lifetime_cache.set(cache_key, resp)
    _log.debug("[stats] lifetime fetched for %s@%s", scrub(name), world)
    return resp
