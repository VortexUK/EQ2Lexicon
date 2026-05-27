"""
GET /api/rankings/filters  — the smart-dropdown tree (scopes -> zones -> bosses).
GET /api/rankings          — a ranked board for one (size, zone, boss, metric[, class]).

Computed-on-read over the existing parses tables (no separate ranking store).
Boss kills are detected with parses.boss.is_boss, mirror-grouped to their
primary upload, then ranked. Soft-deleted parses still rank (the leaderboard
ignores hidden_at); only a hard purge removes them. See
docs/superpowers/specs/2026-05-25-eq2logs-rankings-design.md.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections import defaultdict
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from census import zones_db
from parses import db as parses_db
from parses.boss import is_boss
from web.auth_deps import require_user_session as _require_user
from web.cache import TTLCache
from web.limiter import limiter
from web.routes.parses import _PLAYER_COUNT_SQL, _group_into_fights
from web.server_context import current_server

router = APIRouter(tags=["rankings"])

# Valid ?size= keys + the GROUP player-count range. Raid is deliberately
# open-ended (anything above the group max) — EQ2 ACT tallies mercs, pets and
# swap-ins as "players", so a 24-player raid routinely counts higher (a real
# Wuoshi kill counted 30). The raid tuple's upper bound is nominal/display
# only; _scope_for never caps raid. The table's Size column shows the real count.
_SCOPES: dict[str, tuple[int, int]] = {"group": (2, 6), "raid": (7, 24)}
_SCOPE_LABELS = {"group": "Group", "raid": "Raid"}
_METRIC_FIELD = {"dps": "encdps", "hps": "enchps"}  # speed handled separately

# Short-lived cache of the expensive load+group step (boards are cheap on top).
rankings_cache: TTLCache = TTLCache(ttl=60, max_age=600, name="rankings", maxsize=4)
_KILLS_KEY = "primary_boss_kills"


def _apply_percentiles(rows: list[dict], *, score_key: str, higher_better: bool) -> None:
    """Set each row's 'percentile' relative to the board LEADER: the best row is
    100, the rest scale by how close their score is to it. Computed over exactly
    the rows passed (i.e. after any class filter), so the top of whatever is
    displayed reads 100% and everyone else is measured against that one.

    higher_better=True for DPS/HPS (bigger wins); False for Speed (lower time
    wins, so percentile = fastest_time / this_time)."""
    vals = [r[score_key] for r in rows if r.get(score_key)]
    if not vals:
        for r in rows:
            r["percentile"] = 0
        return
    if higher_better:
        top = max(vals)
        for r in rows:
            r["percentile"] = round(100 * (r.get(score_key) or 0) / top) if top else 0
    else:
        best = min(vals)
        for r in rows:
            v = r.get(score_key) or 0
            r["percentile"] = round(100 * best / v) if v else 0


def _scope_for(player_count: int) -> str | None:
    # 1 = solo (excluded), 2-6 = group, 7+ = raid (no upper cap — see _SCOPES).
    group_lo, group_hi = _SCOPES["group"]
    if player_count > group_hi:
        return "raid"
    if player_count >= group_lo:
        return "group"
    return None


def _is_player_combatant(c: dict) -> bool:
    name = (c.get("name") or "").strip()
    return bool(c.get("ally")) and bool(name) and " " not in name and name != "Unknown"


def _build_character_board(
    kills: list[dict], *, size: str, zone: str, boss: str, metric: str
) -> tuple[list[dict], list[str]]:
    """Per-character best for Damage/Healing. Returns (rows sorted by score
    desc, sorted class list). Percentile is computed within each class."""
    field = _METRIC_FIELD.get(metric)
    if field is None:
        raise ValueError(f"Unsupported metric for character board: {metric!r}")
    best: dict[str, dict] = {}  # name.lower() -> entry
    for k in kills:
        if k["scope"] != size or k["zone"] != zone or k["title"] != boss:
            continue
        for c in k["combatants"]:
            if not _is_player_combatant(c) or not c.get("cls"):
                continue
            score = c.get(field) or 0.0
            key = c["name"].strip().lower()
            cur = best.get(key)
            if cur is None or score > cur["score"]:
                best[key] = {
                    "kind": "character",
                    "name": c["name"].strip(),
                    "guild_name": c.get("guild_name"),
                    "level": c.get("level"),
                    "cls": c["cls"],
                    "ilvl": c.get("ilvl"),
                    "score": score,
                    "encounter_id": k["id"],
                    "size": k["player_count"],
                    "started_at": k["started_at"],
                }
    entries = list(best.values())
    entries.sort(key=lambda e: e["score"], reverse=True)
    classes = sorted({e["cls"] for e in entries})
    # Percentiles are applied by the route via _apply_percentiles, after any
    # class filter, so the top of the displayed set reads 100%.
    return entries, classes


def _avg_player_ilvl(combatants: list[dict]) -> float | None:
    """Average ilvl of the resolved player combatants in an encounter — the
    'raid ilvl' shown on a guild speed row. None if nobody resolved an ilvl."""
    vals = [c["ilvl"] for c in combatants if _is_player_combatant(c) and c.get("ilvl") is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _build_speed_board(kills: list[dict], *, size: str, zone: str, boss: str) -> list[dict]:
    """Per-guild fastest clear. Returns rows sorted by time asc with percentile."""
    best: dict[str, dict] = {}  # guild.lower() -> entry
    for k in kills:
        if k["scope"] != size or k["zone"] != zone or k["title"] != boss:
            continue
        guild = (k.get("guild_name") or "").strip()
        if not guild:
            continue
        cur = best.get(guild.lower())
        if cur is None or k["duration_s"] < cur["duration_s"]:
            best[guild.lower()] = {
                "kind": "guild",
                "guild_name": guild,
                "duration_s": k["duration_s"],
                "ilvl": _avg_player_ilvl(k["combatants"]),
                "encounter_id": k["id"],
                "size": k["player_count"],
                "started_at": k["started_at"],
            }
    return sorted(best.values(), key=lambda e: e["duration_s"])


@lru_cache(maxsize=1)
def _cached_zones_data() -> tuple[dict[str, list[tuple[str, str]]], list[dict]]:
    """Authoritative zone/boss data from zones.db, built once per process.

    Returns (boss_index, raid_tree):
      * boss_index: ``mob_name_lower -> [(canonical_zone, encounter_name), ...]``
        — the lookup that gates a raid title and maps it to its canonical boss.
      * raid_tree:  ordered ``[{zone, expansion, bosses:[encounter_name,...]}]``
        for raid zones that have encounters (newest expansion first, bosses in
        wing/position order) — drives the dropdowns.

    Empty when zones.db is absent (dev/pre-upload), so everything falls back to
    the is_boss heuristic and parse-derived dropdowns."""
    path = zones_db.DB_PATH
    if not path.exists():
        return {}, []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        boss_index: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for mob_lower, zname, ename in conn.execute(
            """
            SELECT m.mob_name_lower, z.name, e.encounter_name
            FROM zone_encounter_mobs m
            JOIN zone_encounters e ON e.id = m.encounter_id
            JOIN zones z ON z.id = e.zone_id
            """
        ):
            boss_index[mob_lower].append((zname, ename))
        raid_tree: list[dict] = []
        for zid, zname, exp, exp_name in conn.execute(
            """
            SELECT z.id, z.name, z.expansion_short, z.expansion_name
            FROM zones z
            WHERE z.id IN (SELECT DISTINCT zone_id FROM zone_encounters)
            ORDER BY z.expansion_year DESC, z.name
            """
        ):
            bosses = [
                r[0]
                for r in conn.execute(
                    "SELECT encounter_name FROM zone_encounters WHERE zone_id = ? ORDER BY position",
                    (zid,),
                )
            ]
            raid_tree.append({"zone": zname, "expansion": exp, "expansion_name": exp_name, "bosses": bosses})
        return dict(boss_index), raid_tree
    finally:
        conn.close()


def _resolve_boss(title: str, zone: str | None, scope: str) -> tuple[bool, str | None, str | None]:
    """Whether an encounter is a rankable boss, and its canonical (zone, title).

    RAIDS: zones.db is authoritative — a title matching a known raid encounter
    mob is a boss, remapped to its canonical zone + encounter name (so ACT
    zone-name variance collapses to one board). Unpopulated raid content and all
    group/dungeon content fall back to the is_boss heuristic, keeping the ACT
    zone/title."""
    if scope == "raid":
        boss_index, _ = _cached_zones_data()
        candidates = boss_index.get(title.lower())
        if candidates:
            if len(candidates) > 1 and zone:
                resolved = zones_db.find_by_name(zone)
                if resolved:
                    for cz, ct in candidates:
                        if cz == resolved["name"]:
                            return True, cz, ct
            cz, ct = candidates[0]
            return True, cz, ct
    if is_boss(title):
        return True, zone, title
    return False, zone, title


def _build_filters(kills: list[dict]) -> dict:
    """Scope -> zone -> boss tree for the dropdowns. Raid zones/bosses come from
    zones.db (authoritative, full structure including bosses with no kills yet),
    each tagged with its expansion; group/dungeon zones come from the uploaded
    kills. Heuristic-matched raid kills for zones not yet in zones.db are appended
    under an "Other" expansion so they still appear.

    Also returns ``raid_expansions`` (newest first) and a ``default_expansion``
    for the expansion selector — the SERVER_CURRENT_XPAC env var if it has raids,
    else the most recent expansion that does."""
    _, raid_tree = _cached_zones_data()
    raid_zones: dict[str, dict] = {}  # insertion-ordered: zone -> {bosses, expansion}
    exp_names: dict[str, str] = {}  # short -> display name
    exp_order: list[str] = []  # distinct expansion shorts, newest first
    for entry in raid_tree:
        raid_zones[entry["zone"]] = {"bosses": list(entry["bosses"]), "expansion": entry["expansion"]}
        short = entry["expansion"]
        if short and short not in exp_names:
            exp_names[short] = entry.get("expansion_name") or short
            exp_order.append(short)

    group_zones: dict[str, set] = defaultdict(set)
    has_other_raid = False
    for k in kills:
        zone = k.get("zone") or "(unknown zone)"
        if k.get("scope") == "raid":
            z = raid_zones.setdefault(zone, {"bosses": [], "expansion": None})
            if z["expansion"] is None:
                z["expansion"] = "Other"
                has_other_raid = True
            if k["title"] not in z["bosses"]:
                z["bosses"].append(k["title"])
        elif k.get("scope") == "group":
            group_zones[zone].add(k["title"])

    raid_expansions = [{"short": s, "name": exp_names[s]} for s in exp_order]
    if has_other_raid:
        raid_expansions.append({"short": "Other", "name": "Other"})

    # current_server().current_xpac may be the short code ("EoF") or the full
    # expansion name ("Echoes of Faydwer"), case-insensitive; unknown/unset →
    # most recent.
    srv_xpac = (current_server().current_xpac or "").strip().lower()
    default_expansion = next(
        (e["short"] for e in raid_expansions if srv_xpac in (e["short"].lower(), e["name"].lower())),
        raid_expansions[0]["short"] if raid_expansions else None,
    )

    scopes: list[dict] = []
    if raid_zones:
        scopes.append(
            {
                "key": "raid",
                "label": _SCOPE_LABELS["raid"],
                "zones": [
                    {"zone": z, "bosses": v["bosses"], "expansion": v["expansion"]} for z, v in raid_zones.items()
                ],
            }
        )
    if group_zones:
        scopes.append(
            {
                "key": "group",
                "label": _SCOPE_LABELS["group"],
                "zones": [{"zone": z, "bosses": sorted(b), "expansion": None} for z, b in sorted(group_zones.items())],
            }
        )
    return {"scopes": scopes, "raid_expansions": raid_expansions, "default_expansion": default_expansion}


def _load_primary_boss_kills() -> list[dict]:
    """Load winning boss-kill encounters, mirror-group them, and return one
    'primary' (longest) upload per fight with its combatants attached. Ignores
    hidden_at so soft-deleted parses still rank. Called from an executor by the
    async endpoints."""
    if not parses_db.DB_PATH.exists():
        return []
    conn = parses_db.init_db(parses_db.DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT e.id, e.title, e.zone, e.guild_name, e.uploaded_by,
                   e.started_at, e.duration_s, e.success_level,
                   ({_PLAYER_COUNT_SQL}) AS player_count
            FROM encounters e
            WHERE e.success_level = 1
            ORDER BY e.started_at DESC
            """
        ).fetchall()
        # Gate + canonicalise per row (scope is known from player_count): raid
        # bosses resolve against zones.db, everything else via the heuristic.
        encs: list[dict] = []
        for r in rows:
            d = dict(r)
            scope = _scope_for(d.get("player_count") or 0)
            if scope is None:
                continue
            ok, czone, ctitle = _resolve_boss(d["title"], d["zone"], scope)
            if not ok:
                continue
            d["zone"] = czone
            d["title"] = ctitle
            encs.append(d)
        kills: list[dict] = []
        for g in _group_into_fights(encs):
            scope = _scope_for(g.get("player_count") or 0)
            if scope is None:
                continue
            kills.append(
                {
                    "id": g["id"],
                    "title": g["title"],
                    "zone": g["zone"],
                    "guild_name": g.get("guild_name"),
                    "started_at": g["started_at"],
                    "duration_s": g["duration_s"],
                    "player_count": g.get("player_count") or 0,
                    "scope": scope,
                    "combatants": parses_db.get_combatants_for_encounter(conn, g["id"]),
                }
            )
        return kills
    finally:
        conn.close()


def _cached_kills() -> list[dict]:
    cached = rankings_cache.get(_KILLS_KEY)
    if cached is not None:
        return cached
    kills = _load_primary_boss_kills()
    rankings_cache.set(_KILLS_KEY, kills)
    return kills


def benchmarks_for_boss(boss_title: str) -> dict[str, tuple[dict[str, float], float]]:
    """Best encDPS and encHPS achieved per class, and overall, for a boss across
    all primary winning kills (the rankings dataset). Lets the parse page colour
    each combatant's encDPS/encHPS by where it sits among their class for that
    boss (class leader = 100%), and flag the all-class best. Returns
    {"dps": ({class: best}, overall), "hps": ({class: best}, overall)}."""
    dps_by_class: dict[str, float] = {}
    hps_by_class: dict[str, float] = {}
    dps_overall = 0.0
    hps_overall = 0.0
    for k in _cached_kills():
        if k["title"] != boss_title:
            continue
        for c in k["combatants"]:
            if not _is_player_combatant(c) or not c.get("cls"):
                continue
            cls = c["cls"]
            dps = c.get("encdps") or 0.0
            hps = c.get("enchps") or 0.0
            if dps > dps_by_class.get(cls, 0.0):
                dps_by_class[cls] = dps
            dps_overall = max(dps_overall, dps)
            if hps > hps_by_class.get(cls, 0.0):
                hps_by_class[cls] = hps
            hps_overall = max(hps_overall, hps)
    return {"dps": (dps_by_class, dps_overall), "hps": (hps_by_class, hps_overall)}


class RankingRow(BaseModel):
    kind: str  # "character" | "guild"
    encounter_id: int
    percentile: int
    size: int
    started_at: int
    # character rows
    name: str | None = None
    guild_name: str | None = None
    level: int | None = None
    cls: str | None = None
    score: float | None = None
    # guild rows (Speed)
    duration_s: int | None = None
    # ilvl: the character's snapshot for character rows; the raid average for guild rows
    ilvl: float | None = None


class RankingsResponse(BaseModel):
    rows: list[RankingRow]
    classes: list[str]
    total: int


@router.get("/rankings/filters")
@limiter.limit("60/minute")
async def get_ranking_filters(request: Request) -> dict:
    _require_user(request)
    loop = asyncio.get_event_loop()
    kills = await loop.run_in_executor(None, _cached_kills)
    return _build_filters(kills)


@router.get("/rankings", response_model=RankingsResponse)
@limiter.limit("60/minute")
async def get_rankings(
    request: Request,
    size: str,
    zone: str,
    boss: str,
    metric: str,
    class_name: str | None = Query(None, alias="class"),
) -> RankingsResponse:
    _require_user(request)
    if size not in _SCOPES:
        raise HTTPException(status_code=400, detail="size must be 'raid' or 'group'")
    if metric not in ("dps", "hps", "speed"):
        raise HTTPException(status_code=400, detail="metric must be 'dps', 'hps' or 'speed'")

    loop = asyncio.get_event_loop()
    kills = await loop.run_in_executor(None, _cached_kills)

    if metric == "speed":
        rows = _build_speed_board(kills, size=size, zone=zone, boss=boss)
        _apply_percentiles(rows, score_key="duration_s", higher_better=False)
        classes: list[str] = []
    else:
        rows, classes = _build_character_board(kills, size=size, zone=zone, boss=boss, metric=metric)
        if class_name:
            rows = [r for r in rows if r["cls"] == class_name]
        _apply_percentiles(rows, score_key="score", higher_better=True)

    return RankingsResponse(
        rows=[RankingRow(**r) for r in rows],
        classes=classes,
        total=len(rows),
    )
