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

import sqlite3
import unicodedata
from collections import defaultdict
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from backend.census.constants import FIGHTERS, MAGES, PRIESTS, SCOUTS
from backend.eq2db.zones import catalogue as zones_db
from backend.server.api.parses.list import _PLAYER_COUNT_SQL, _group_into_fights
from backend.server.auth_deps import require_user_session as _require_user
from backend.server.cache import TTLCache
from backend.server.core.executor import run_sync
from backend.server.limiter import limiter
from backend.server.parses import db as parses_db
from backend.server.parses.boss import is_boss
from backend.server.server_context import current_server, current_world
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)

router = APIRouter(tags=["rankings"])

# The `class` rankings filter accepts either a single class name or one of these
# archetypes — selecting an archetype ranks every class under it. Keys match the
# `archetype` field served by /api/classes (singular: Fighter/Priest/Scout/Mage),
# so the frontend can group the dropdown and send the archetype straight through.
_ARCHETYPE_CLASSES: dict[str, frozenset[str]] = {
    "Fighter": FIGHTERS,
    "Priest": PRIESTS,
    "Scout": SCOUTS,
    "Mage": MAGES,
}

# Mirror of frontend normaliseBossName in RankingsPage.tsx — keep in sync.
# Folds the full set of apostrophe-like and space-like Unicode codepoints
# we've seen in ACT logs and curator-entered roster data so the boss_index
# lookup doesn't silently miss on codepoint mismatches.
_APOSTROPHE_VARIANTS = str.maketrans(
    {
        "`": "'",  # U+0060 GRAVE ACCENT
        "´": "'",  # U+00B4 ACUTE ACCENT
        "ʹ": "'",  # U+02B9 MODIFIER LETTER PRIME
        "ʺ": "'",  # U+02BA MODIFIER LETTER DOUBLE PRIME
        "ʻ": "'",  # U+02BB MODIFIER LETTER TURNED COMMA
        "ʼ": "'",  # U+02BC MODIFIER LETTER APOSTROPHE
        "ʽ": "'",  # U+02BD MODIFIER LETTER REVERSED COMMA
        "ʾ": "'",  # U+02BE MODIFIER LETTER RIGHT HALF RING
        "ʿ": "'",  # U+02BF MODIFIER LETTER LEFT HALF RING
        "ˈ": "'",  # U+02C8 MODIFIER LETTER VERTICAL LINE
        "‘": "'",  # U+2018 LEFT SINGLE QUOTATION MARK
        "’": "'",  # U+2019 RIGHT SINGLE QUOTATION MARK
        "‛": "'",  # U+201B SINGLE HIGH-REVERSED-9 QUOTATION MARK
        "′": "'",  # U+2032 PRIME
        "＇": "'",  # U+FF07 FULLWIDTH APOSTROPHE
        " ": " ",  # U+00A0 NO-BREAK SPACE
        " ": " ",  # U+2009 THIN SPACE
        " ": " ",  # U+200A HAIR SPACE
        " ": " ",  # U+202F NARROW NO-BREAK SPACE
        " ": " ",  # U+205F MEDIUM MATHEMATICAL SPACE
        "　": " ",  # U+3000 IDEOGRAPHIC SPACE
    }
)


def _normalise_boss_key(s: str) -> str:
    """Lowercase + Unicode NFC + collapse apostrophe/space variants. Used
    as the cache-key shape for boss_index lookups so curator-entered and
    parse-shipped codepoint variants can never silently miss each other.
    Frontend mirror: normaliseBossName in RankingsPage.tsx."""
    return unicodedata.normalize("NFC", s).lower().translate(_APOSTROPHE_VARIANTS).strip()


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


def _build_speed_board_character(
    kills: list[dict],
    *,
    zone: str,
    boss: str,
) -> list[dict]:
    """Per-character fastest-clear board.

    Used for dungeon Speed rankings (scope=group). Each ally combatant
    flagged ``is_player=1`` on the kill gets one row showing the fastest
    duration of any clear they were on. If 6 friends speedrun a dungeon
    together in 1m23s, all 6 rows tie at 83s — the right answer for
    mixed-guild groups where the per-guild aggregation in
    ``_build_speed_board`` is meaningless.

    Filters: zone + boss match (the canonical leaderboard predicate). The
    scope filter is implicit — caller passes the kills already gated by
    the dungeon scope, so we don't re-check here.

    Returns rows sorted by duration ascending (fastest first), then by
    name ASC as a stable tiebreaker."""
    best: dict[str, dict] = {}
    for k in kills:
        if k["zone"] != zone or k["title"] != boss:
            continue
        for c in k["combatants"]:
            if not c.get("is_player"):
                continue
            name = c.get("name") or ""
            if not name:
                continue
            cur = best.get(name)
            if cur is None or k["duration_s"] < cur["duration_s"]:
                best[name] = {
                    "kind": "character",
                    "name": name,
                    "cls": c.get("cls"),
                    "duration_s": k["duration_s"],
                    "ilvl": _avg_player_ilvl(k["combatants"]),
                    "encounter_id": k["id"],
                    "size": k["player_count"],
                    "started_at": k["started_at"],
                }
    return sorted(best.values(), key=lambda r: (r["duration_s"], r["name"]))


def invalidate_zones_cache() -> None:
    """Clear the _cached_zones_data lru_cache AND the parses
    classifier's leaderboard map AND mark every combatant for
    re-classification.

    Call this after any mutation to zones / zone_encounters /
    zone_encounter_mobs so:
      * the next /api/rankings/filters rebuilds the dropdown tree
      * the next /api/parses request rebuilds the classifier map
      * existing parses re-classify against the updated zone trees
        on first read (the brute-force is_player NULL reset is fine
        at current data size — flagged as a scalability concern in
        the pet-detection-pipeline spec)
    """
    _cached_zones_data.cache_clear()
    # Local imports: parses.list already imports _cached_zones_data
    # from this module, and parses.db is a deeper dependency. Local
    # imports keep the module-load DAG cycle-free.
    from backend.server.api.parses.list import _classifier_cache_clear
    from backend.server.parses import db as parses_db

    _classifier_cache_clear()
    parses_db.invalidate_is_player_cache()


@lru_cache(maxsize=1)
def _cached_zones_data() -> tuple[dict[str, list[tuple[str, str]]], list[dict], list[dict]]:
    """Authoritative zone/boss data from zones.db, built once per process.

    Returns (boss_index, raid_tree, dungeon_tree):
      * boss_index: ``mob_name_lower -> [(canonical_zone, encounter_name), ...]``
        — the lookup that gates a raid title and maps it to its canonical boss.
      * raid_tree:    ordered ``[{zone, expansion, bosses:[...]}]`` for zones
                      with the ``raid_x4`` type. Drives the Raids dropdown.
      * dungeon_tree: same shape, for zones with the ``dungeon`` type overlay
                      (curated max-level group instances). Drives the
                      Dungeons dropdown.

    The two trees are deliberately separate even though raid_tree used to
    contain everything with encounters — without the split a curated dungeon
    that happens to have bosses (which they all do, post-PR #36) would show
    under the "Raids" dropdown alongside the actual raids.

    Empty when zones.db is absent (dev/pre-upload), so everything falls back
    to the is_boss heuristic and parse-derived dropdowns.

    PROCESS-LOCAL: this LRU lives in one Python process. invalidate_zones_cache()
    only clears it on the worker that handled the mutation; sibling workers
    serve stale data until they happen to evict. A startup assertion in
    web/app.py:_startup pins WEB_CONCURRENCY=1 so this is safe — if that
    assertion is ever loosened, swap this for an mtime-based reload (compare
    ``zones.db.stat().st_mtime`` against the cached value on each call) or
    move invalidation to a Redis-backed fan-out."""
    path = zones_db.path
    if not path.exists():
        return {}, [], []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        boss_index: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for mob_lower, zname, ename in conn.execute(_SQL["list_all_zone_encounter_mobs"]):
            boss_index[_normalise_boss_key(mob_lower)].append((zname, ename))

        def _tree_for_type(type_token: str) -> list[dict]:
            """Materialise the (zone, expansion, bosses) ordered list for one
            zone-type token. Joins zones → zone_types so the same query
            powers both the raid and dungeon trees with no duplication."""
            out: list[dict] = []
            for zid, zname, exp, exp_name in conn.execute(
                _SQL["list_zones_by_type_with_encounters"],
                (type_token,),
            ):
                bosses = [r[0] for r in conn.execute(_SQL["list_encounter_names_for_zone"], (zid,))]
                out.append({"zone": zname, "expansion": exp, "expansion_name": exp_name, "bosses": bosses})
            return out

        return dict(boss_index), _tree_for_type("raid_x4"), _tree_for_type("dungeon")
    finally:
        conn.close()


def _resolve_boss(title: str, zone: str | None, scope: str) -> tuple[bool, str | None, str | None]:
    """Whether an encounter is a rankable boss, and its canonical (zone, title).

    For both raid AND group scopes, zones.db is authoritative — a title
    matching a known curated encounter mob is a boss, remapped to its
    canonical zone + encounter name. This collapses:
      * ACT zone-name variance (different log lines for the same zone)
      * Multi-mob encounters (killing any of the mobs in a curated
        encounter resolves to the same (zone, encounter_name), so the
        rankings page shows one entry per encounter rather than one
        per mob)

    Unpopulated zones fall back to the is_boss heuristic, keeping the
    ACT zone/title verbatim — this is how rankings surface kills for
    zones the curator hasn't gotten to yet."""
    if scope in ("raid", "group"):
        boss_index, _, _ = _cached_zones_data()
        candidates = boss_index.get(_normalise_boss_key(title))
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
    """Scope → zone → boss tree for the dropdowns.

    Two sources of truth, both from zones.db:

      * **Raid** zones/bosses come from the ``raid_x4`` type — full structure
        including bosses with no kills yet, each tagged with its expansion.
        Heuristic-matched raid kills for zones not yet in zones.db are
        appended under an "Other" expansion so they still appear.
      * **Dungeon** zones/bosses come from the ``dungeon`` type overlay (the
        curated max-level group instances). All curated dungeons appear in
        the dropdown even when zero kills have been uploaded yet, so the
        viewer sees the full tracked set. Group-scope kills for zones NOT in
        the curated set are dropped from the dropdown (still in the DB —
        they just don't pollute the rankings UI).

    Also returns ``raid_expansions`` (newest first) and ``default_expansion``
    for the expansion selector — the server's current_xpac when it has raids,
    else the most recent expansion that does."""
    _, raid_tree, dungeon_tree = _cached_zones_data()

    raid_zones: dict[str, dict] = {}  # insertion-ordered: zone -> {bosses, expansion}
    exp_names: dict[str, str] = {}  # short -> display name
    exp_order: list[str] = []  # distinct expansion shorts, newest first
    for entry in raid_tree:
        raid_zones[entry["zone"]] = {"bosses": list(entry["bosses"]), "expansion": entry["expansion"]}
        short = entry["expansion"]
        if short and short not in exp_names:
            exp_names[short] = entry.get("expansion_name") or short
            exp_order.append(short)

    # Curated dungeons — keyed identically to raid_zones so the per-zone
    # shape downstream is consistent. Bosses come straight from zones.db
    # (the curated 3–11 per zone for EoF), not from kill data.
    dungeon_zones: dict[str, dict] = {}
    for entry in dungeon_tree:
        dungeon_zones[entry["zone"]] = {"bosses": list(entry["bosses"]), "expansion": entry["expansion"]}
        short = entry["expansion"]
        if short and short not in exp_names:
            exp_names[short] = entry.get("expansion_name") or short
            exp_order.append(short)

    has_other_raid = False
    for k in kills:
        if k.get("scope") != "raid":
            # Non-raid kills (group-scope, etc.) no longer build the
            # dropdown — see the dungeon-curation block above. They're
            # still queryable for the leaderboard once a zone is selected.
            continue
        zone = k.get("zone") or "(unknown zone)"
        z = raid_zones.setdefault(zone, {"bosses": [], "expansion": None})
        if z["expansion"] is None:
            z["expansion"] = "Other"
            has_other_raid = True
        if k["title"] not in z["bosses"]:
            z["bosses"].append(k["title"])

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
    if dungeon_zones:
        scopes.append(
            {
                "key": "group",
                "label": _SCOPE_LABELS["group"],
                # ``expansion`` populated per-zone so the frontend can filter
                # the dungeon dropdown by the selected xpac, parallel to how
                # the raid dropdown is filtered.
                "zones": [
                    {"zone": z, "bosses": v["bosses"], "expansion": v["expansion"]} for z, v in dungeon_zones.items()
                ],
            }
        )
    return {"scopes": scopes, "raid_expansions": raid_expansions, "default_expansion": default_expansion}


def _load_primary_boss_kills(world: str = "Varsoon") -> list[dict]:
    """Load winning boss-kill encounters, mirror-group them, and return one
    'primary' (longest) upload per fight with its combatants attached. Ignores
    hidden_at so soft-deleted parses still rank. Called from an executor by the
    async endpoints.

    ``world`` scopes to the active server so each server sees only its own
    leaderboard data."""
    if not parses_db.DB_PATH.exists():
        return []
    conn = parses_db.init_db(parses_db.DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            _SQL["list_winning_encounters_with_player_count"].format(player_count_sql=_PLAYER_COUNT_SQL),
            (world,),
        ).fetchall()
        # Phase 4 lazy backfill: classify combatants for any encounter
        # whose is_player flag is still NULL (pre-migration historic
        # data). The player_count in the SELECT above uses the same
        # _PLAYER_COUNT_SQL subquery as parses_list — refresh it here
        # so the post-classifier value drives _scope_for below.
        from backend.server.api.parses.list import _ensure_classified  # noqa: PLC0415 — local: avoid import cycle

        rows = [dict(r) for r in rows]
        for r in rows:
            if _ensure_classified(conn, r["id"], r["zone"]):
                refreshed = conn.execute(
                    _SQL["count_player_combatants_for_encounter"],
                    (r["id"],),
                ).fetchone()
                r["player_count"] = int(refreshed[0])
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
        for g in _group_into_fights(encs, conn):
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


def _cached_kills(world: str | None = None) -> list[dict]:
    """Return the cached boss-kill list for ``world`` (defaults to
    current_world() when called inside a request context).  The cache key
    is per-world so each server's leaderboard is independently cached."""
    effective_world = world or current_server().world
    cache_key = f"{_KILLS_KEY}:{effective_world}"
    cached = rankings_cache.get(cache_key)
    if cached is not None:
        return cached
    kills = _load_primary_boss_kills(effective_world)
    rankings_cache.set(cache_key, kills)
    return kills


def benchmarks_for_boss(boss_title: str, world: str | None = None) -> dict[str, tuple[dict[str, float], float]]:
    """Best encDPS and encHPS achieved per class, and overall, for a boss across
    all primary winning kills (the rankings dataset). Lets the parse page colour
    each combatant's encDPS/encHPS by where it sits among their class for that
    boss (class leader = 100%), and flag the all-class best. Returns
    {"dps": ({class: best}, overall), "hps": ({class: best}, overall)}.

    ``world`` defaults to current_world() — pass explicitly when calling from
    a thread where the contextvar may not be propagated."""
    dps_by_class: dict[str, float] = {}
    hps_by_class: dict[str, float] = {}
    dps_overall = 0.0
    hps_overall = 0.0
    for k in _cached_kills(world):
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
    # Resolve world in the async handler (contextvar is set here); do NOT read
    # current_world() inside the executor thread — contextvars don't cross threads.
    world = current_world()
    kills = await run_sync(_cached_kills, world)
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

    # Resolve world in the async handler (contextvar is set here); do NOT read
    # current_world() inside the executor thread — contextvars don't cross threads.
    world = current_world()
    kills = await run_sync(_cached_kills, world)

    if metric == "speed":
        if size == "group":
            rows = _build_speed_board_character(kills, zone=zone, boss=boss)
        else:
            rows = _build_speed_board(kills, size=size, zone=zone, boss=boss)
        _apply_percentiles(rows, score_key="duration_s", higher_better=False)
        classes: list[str] = []
    else:
        rows, classes = _build_character_board(kills, size=size, zone=zone, boss=boss, metric=metric)
        if class_name:
            members = _ARCHETYPE_CLASSES.get(class_name)
            if members is not None:
                rows = [r for r in rows if r["cls"] in members]
            else:
                rows = [r for r in rows if r["cls"] == class_name]
        _apply_percentiles(rows, score_key="score", higher_better=True)

    return RankingsResponse(
        rows=[RankingRow(**r) for r in rows],
        classes=classes,
        total=len(rows),
    )
