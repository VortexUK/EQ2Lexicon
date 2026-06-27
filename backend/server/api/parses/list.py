"""GET /parses + GET /parses/{id} — paginated list + detail of recent encounters.

Carved out of the original 1687-line web/routes/parses.py. All helpers used
ONLY by the read paths live here. Helpers shared with ingest live in
ingest.py (and the read paths import them).
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from fastapi import HTTPException, Request

from backend.eq2db import zones as zones_db
from backend.server import db as users_db
from backend.server.api.parses import router  # the package-level router
from backend.server.api.parses.models import (
    AttackSummary,
    CombatantSummary,
    CureSummary,
    DamageTypeBreakdown,
    HealSummary,
    ParseDetailResponse,
    ParseEncounterSummary,
    ParsePermissions,
    ParsesListResponse,
    ParseUploadSummary,
    ThreatSummary,
)
from backend.server.auth_deps import (
    is_admin as _is_admin,
)
from backend.server.auth_deps import (
    require_user_session as _require_user,
)
from backend.server.constants import (
    PARSE_INNER_CAP_FLOOR,
    PARSE_INNER_CAP_MULTIPLIER,
    PARSE_LIST_MAX_LIMIT,
    PARSE_MIRROR_WINDOW_S,
)
from backend.server.core.executor import run_sync
from backend.server.limiter import limiter
from backend.server.parses import db as parses_db
from backend.server.parses.pet_detection import classify_combatants
from backend.server.server_context import current_world
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


def _uploader_discord_id(source_dsn: str | None) -> str | None:
    """At ingest, plugin uploads stamp source_dsn as 'plugin:<discord_id>'.
    Returns the discord ID for plugin-uploaded rows, None for local ingests
    or malformed values."""
    if not source_dsn or not source_dsn.startswith("plugin:"):
        return None
    return source_dsn[len("plugin:") :] or None


# Encounter "size" buckets — mapped to a (min_players, max_players) range
# inclusive on both ends. Used to filter the list endpoint via ?size=...
SIZE_BUCKETS: Mapping[str, tuple[int, int]] = MappingProxyType(
    {
        "individual": (1, 1),
        "group": (2, 6),
        "raid12": (7, 12),
        "raid24": (13, 24),
    }
)

# Player count: ally combatants flagged is_player=1 by the pet-detection
# pipeline (see parses/pet_detection.py). Pre-Phase-4 historic rows
# have is_player=NULL until _ensure_classified backfills them on first
# read — until then they count as 0, which is fine because the lazy-
# backfill runs BEFORE the SQL filter in every read path.
# Subquery fragment used by both the encounter-listing query (below) and
# rankings.py's leaderboard loader. Defined here so the two call sites
# share one source of truth. Re-exported under the original
# `_PLAYER_COUNT_SQL` name for backwards compat with rankings.py's import.
_PLAYER_COUNT_SQL = _SQL["player_count_subquery"]
_TOP_N_ALLY_SQL = _SQL["top_n_ally_names"]
_ALL_ALLY_SQL = _SQL["all_ally_names"]


def _ensure_classified(conn: sqlite3.Connection, encounter_id: int, zone: str | None) -> bool:
    """Lazy backfill for pre-Phase-4 combatant rows.

    If any combatant for this encounter has ``is_player IS NULL`` (i.e.
    was inserted before the pet-detection pipeline shipped), run the
    classifier now and persist. No-op when every row is already
    classified (a single indexed lookup — steady-state cost is
    negligible).

    Called by every read path (parses list, parse detail, rankings load)
    before any ``WHERE is_player = 1`` query that depends on the value
    being populated. Without this, historic encounters would silently
    report player_count=0 forever.

    Returns True iff backfill actually ran (so callers can decide
    whether to re-query player_count for the same response)."""
    needs = conn.execute(_SQL["has_unclassified_combatants"], (encounter_id,)).fetchone()
    if not needs:
        return False
    rows = parses_db.get_combatants_for_encounter(conn, encounter_id)
    zone_category = _classify_zone(zone)
    classification = classify_combatants(rows, zone_category)
    parses_db.update_combatant_is_player(conn, classification)
    conn.commit()
    return True


def _top_n_ally_names(conn: sqlite3.Connection, encounter_id: int, n: int) -> set[str]:
    """Return the top-N player names in this encounter by encDPS descending.

    Tiebreaker on name ASC so two combatants with identical encDPS pick the
    same N — important because the merger uses ``set ==`` semantics on these
    lists and a flapping last slot would break determinism.

    Returns ``min(n, available)`` names if the encounter has fewer qualifying
    allies than ``n``. Empty set when there are no qualifying allies at all
    (e.g. an empty-ally parse) — that case still merges trivially under the
    Phase 4 mutual-containment rule (``set() ⊆ X`` is always true)."""
    return {row[0] for row in conn.execute(_TOP_N_ALLY_SQL, (encounter_id, n))}


def _all_ally_names(conn: sqlite3.Connection, encounter_id: int) -> set[str]:
    """Every qualifying player name in the encounter. Pairs with
    ``_top_n_ally_names`` to evaluate the merger's mutual-containment rule
    (``top_N(A) ⊆ allies(B)`` and vice versa)."""
    return {row[0] for row in conn.execute(_ALL_ALLY_SQL, (encounter_id,))}


# ── Zone classifier ──────────────────────────────────────────────────────
# Bucket a parse's zone into Raid / Dungeon / Other for the ParsesPage
# Guild → Category hierarchy. Mirror the rankings page's leaderboard
# predicate exactly so the dropdown set and the classifier set are
# guaranteed in lockstep: a zone counts iff (a) it has the right type AND
# (b) ≥1 row in zone_encounters. _cached_zones_data already embeds (b) in
# the trees it returns, so we just derive the lookup map from those.
#
# _cached_zones_data lives in rankings.py, which already imports from this
# module (_PLAYER_COUNT_SQL, _group_into_fights). A top-level import here
# would create a circular dependency at module load time. Instead we expose
# a thin module-level wrapper that delegates on first call via a local
# import — this lets tests patch 'backend.server.api.parses.list._cached_zones_data'
# while keeping the load-time cycle broken.


def _cached_zones_data() -> tuple[dict, list[dict], list[dict]]:
    """Thin local wrapper around rankings._cached_zones_data.

    Indirects through a local import to avoid the load-time circular
    dependency (rankings → parses.list → rankings). Tests patch THIS name
    in this module's namespace to inject fake zone trees."""
    from backend.server.api.rankings import _cached_zones_data as _real  # noqa: PLC0415

    return _real()


_LEADERBOARD_MAP: dict[str, Literal["raid", "dungeon"]] | None = None


def _classifier_cache_clear() -> None:
    """Reset the lazily-built classifier map. Called from
    rankings.invalidate_zones_cache so the eight admin curator hooks that
    already invalidate the rankings cache also invalidate this one — no
    need to retrofit every call site."""
    global _LEADERBOARD_MAP
    _LEADERBOARD_MAP = None


def _build_leaderboard_map() -> dict[str, Literal["raid", "dungeon"]]:
    """Materialise {zone_name_lower: category} from the cached zone trees.

    Dungeons win ties with raids — neither test data nor real EQ2 data
    should ever assign a single zone BOTH ``raid_x4`` AND ``dungeon``
    types, but if a curator ever does, the rankings page would surface
    it under both dropdowns. Picking "dungeon" here is arbitrary; flag
    this in the audit if it happens in practice."""
    _, raid_tree, dungeon_tree = _cached_zones_data()
    out: dict[str, Literal["raid", "dungeon"]] = {entry["zone"].lower(): "raid" for entry in raid_tree}
    for entry in dungeon_tree:
        out[entry["zone"].lower()] = "dungeon"
    return out


def _classify_zone(zone: str | None) -> Literal["raid", "dungeon", "other"]:
    """Bucket the parse's zone for the Guild → (Raid / Dungeon / Other)
    hierarchy.

    Lookup order:
      1. Empty / None / '(unknown zone)' → 'other'.
      2. Lowercase exact match in the cached leaderboard map.
      3. Alias resolution via ``zones_db.find_by_name`` → retry exact
         match on the canonical name.
      4. Fall through → 'other'.
    """
    if not zone or zone == "(unknown zone)":
        return "other"
    global _LEADERBOARD_MAP
    if _LEADERBOARD_MAP is None:
        _LEADERBOARD_MAP = _build_leaderboard_map()
    hit = _LEADERBOARD_MAP.get(zone.lower())
    if hit is not None:
        return hit
    canonical = zones_db.find_by_name(zone)
    if canonical:
        hit = _LEADERBOARD_MAP.get(canonical["name"].lower())
        if hit is not None:
            return hit
    return "other"


def _list_encounters_sync(
    inner_cap: int,
    zone: str | None,
    size: str | None,
    world: str = "Varsoon",
    search: str | None = None,
) -> list[dict]:
    """Return matching encounter rows most-recent-first, capped at
    ``inner_cap`` raw uploads (not fights). Mirror grouping happens after
    this call — inner_cap must be generous enough to cover the requested
    fight limit × the worst-case mirror count per fight.

    ``world`` scopes results to the active server so a Varsoon viewer only
    sees Varsoon parses."""
    if not parses_db.DB_PATH.exists():
        return []

    # Soft-deleted parses are hidden from the list (but still feed rankings).
    # Note: the WHERE clause operates on the outer query's columns (no alias).
    where_clauses: list[str] = ["hidden_at IS NULL", "world = ?"]
    params: list = [world]
    if zone:
        where_clauses.append("zone = ?")
        params.append(zone)
    if size and size in SIZE_BUCKETS:
        lo, hi = SIZE_BUCKETS[size]
        where_clauses.append("player_count BETWEEN ? AND ?")
        params.extend([lo, hi])
    if search and search.strip():
        # Free-text filter over the user-visible fields, mirroring the admin
        # search (db.list_encounters_for_admin). Applied BEFORE mirror-grouping
        # so a whole fight matches when any of its uploads does.
        like = f"%{search.strip().lower()}%"
        where_clauses.append(
            "(LOWER(title) LIKE ? OR LOWER(IFNULL(zone, '')) LIKE ? "
            "OR LOWER(IFNULL(uploaded_by, '')) LIKE ? OR LOWER(IFNULL(guild_name, '')) LIKE ?)"
        )
        params.extend([like, like, like, like])
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    list_sql = _SQL["list_encounters_recent"].format(where_sql=where_sql, player_count_sql=_PLAYER_COUNT_SQL)

    conn = parses_db.init_db(parses_db.DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(list_sql, [*params, inner_cap]).fetchall()]
    finally:
        conn.close()


def _group_into_fights(encounters: list[dict], conn: sqlite3.Connection) -> list[dict]:
    """Greedy mirror-grouping. Two uploads are the same fight when ALL of:
      - they come from *different* uploaders,
      - their guild + title match,
      - any pair of start times falls within ``PARSE_MIRROR_WINDOW_S``, AND
      - their top-N ally encDPS lists mutually contain each other
        (each side's top-N appears somewhere in the other side's full
        ally list). N = 3 if either upload is in the raid bucket
        (``player_count >= 7``), else 2.

    Same-uploader uploads are never merged — one raider can't mirror their
    own fight, so two of their uploads are two real fights. The canonical
    upload (carried as the top-level fields on the returned dict) is the
    longest-duration upload in the group — the raider whose ACT captured
    the most fight time.

    The top-N gate (added 2026-05-30) catches the case of two of the same
    guild's groups simultaneously doing the same boss — the older gates
    alone would have merged them.

    Each returned group dict looks like::

        {
            # ...all fields of the canonical upload row...
            "uploads": [<every upload dict, including the canonical>],
        }

    The ``conn`` argument lets the top-N gate query ``combatants`` rows
    without re-opening the parses DB per pair. Caller is responsible for
    the connection lifetime."""
    if not encounters:
        return []
    # Sort by started_at ASC so we attach in chronological order — late
    # stragglers reach the group whose existing members include their
    # closest neighbour.
    sorted_encs = sorted(encounters, key=lambda e: e["started_at"])
    groups: list[dict] = []
    for e in sorted_encs:
        attached = False
        for g in groups:
            if g["title"] != e["title"]:
                continue
            if g.get("guild_name") != e.get("guild_name"):
                continue
            # A mirror is the SAME fight captured by a DIFFERENT raider. Two
            # uploads from the same uploader are always distinct fights (a
            # same-encid re-upload is deduped at ingest), so never merge
            # them — even if title/guild/start-time all line up (e.g. the
            # same boss pulled twice within the window).
            if any((u.get("uploaded_by") or "local") == (e.get("uploaded_by") or "local") for u in g["uploads"]):
                continue
            # Compare against every member so a late straggler still attaches
            # even if the first uploader's start time drifted out of window.
            if not any(abs(u["started_at"] - e["started_at"]) <= PARSE_MIRROR_WINDOW_S for u in g["uploads"]):
                continue
            # Top-N mutual containment: each upload's top-N ally encDPS
            # combatants must appear *somewhere* in the other upload's
            # ally list. Prevents two different groups doing the same
            # boss within 60s of each other from merging into one fight
            # when they share guild + title but have entirely different
            # rosters.
            #
            # Compare new upload against the CANONICAL upload in the
            # group. Group membership is overlap-transitive only THROUGH
            # the canonical — every prior member overlapped with the
            # then-canonical at the time of joining, not member-to-
            # member. The canonical can also swap mid-group when a
            # longer-duration upload joins, so the join criterion has a
            # moving target. Both are acceptable for v1 — the
            # pathological case (a new join overlaps the current
            # canonical but would have failed against an earlier
            # member's roster) is rare in practice.
            # Missing-data default of 0 → N=2 (the weaker / more permissive
            # gate). Never fires in practice — _list_encounters_sync's outer
            # SELECT always projects player_count via _PLAYER_COUNT_SQL — but
            # if it ever does, two empty top-N sets trivially merge by the
            # set-containment rule below.
            n = 3 if max(g.get("player_count", 0), e.get("player_count", 0)) >= 7 else 2
            top_e = _top_n_ally_names(conn, e["id"], n)
            all_e = _all_ally_names(conn, e["id"])
            top_g = _top_n_ally_names(conn, g["id"], n)
            all_g = _all_ally_names(conn, g["id"])
            if not (top_e.issubset(all_g) and top_g.issubset(all_e)):
                continue
            g["uploads"].append(e)
            # Promote to canonical if this upload captured a longer fight.
            if e["duration_s"] > g["duration_s"]:
                kept_uploads = g["uploads"]
                g.clear()
                g.update(e)
                g["uploads"] = kept_uploads
            attached = True
            break
        if not attached:
            new_group = dict(e)
            new_group["uploads"] = [e]
            groups.append(new_group)

    # Render order: most-recent fight first.
    groups.sort(key=lambda g: g["started_at"], reverse=True)
    return groups


def _encounter_detail_sync(encounter_id: int, top_attacks_per_combatant: int, world: str = "Varsoon") -> dict | None:
    """Return the encounter + its combatants + top attacks per combatant.

    ``world`` is used to scope the lookup so a viewer on one server can't
    read another server's encounter by guessing its integer id."""
    if not parses_db.DB_PATH.exists():
        return None
    conn = parses_db.init_db()
    try:
        conn.row_factory = sqlite3.Row
        enc_row = conn.execute(_SQL["select_encounter_by_id_and_world"], (encounter_id, world)).fetchone()
        if enc_row is None:
            return None
        enc = dict(enc_row)
        # Phase 4 lazy backfill: classify combatants if pre-migration.
        # is_player drives the frontend Allies/Pets split (Phase 6) and
        # any other consumer that hits the detail endpoint.
        _ensure_classified(conn, enc["id"], enc.get("zone"))

        combatants = parses_db.get_combatants_for_encounter(conn, enc["id"])
        for c in combatants:
            c["top_attacks"] = parses_db.get_top_attacks_for_combatant(conn, c["id"], limit=top_attacks_per_combatant)
            c["top_heals"] = parses_db.get_top_heals_for_combatant(conn, c["id"], limit=top_attacks_per_combatant)
            c["top_cures"] = parses_db.get_top_cures_for_combatant(conn, c["id"], limit=top_attacks_per_combatant)
            c["top_threats"] = parses_db.get_top_threats_for_combatant(conn, c["id"], limit=top_attacks_per_combatant)
            c["damage_types"] = parses_db.get_damage_types_for_combatant(conn, c["id"])
            c["ally"] = bool(c["ally"])
            c["is_player"] = bool(c.get("is_player"))
        enc["combatants"] = combatants
        return enc
    finally:
        conn.close()


async def _compute_permissions(
    request: Request,
    encounters: list[dict],
) -> dict[int, ParsePermissions]:
    """Return {encounter_id: ParsePermissions} for the rendered list. Admin
    short-circuits all-true; otherwise we run one cached officer check per
    unique guild that appears in the result set, then combine with the
    uploader match."""
    user = request.session.get("user")
    if not user:
        return {e["id"]: ParsePermissions() for e in encounters}

    if _is_admin(user):
        return {e["id"]: ParsePermissions(can_delete=True) for e in encounters}

    # Local import to dodge any circular dependency through web.routes.guild.
    from backend.server.api.guild import _officer_chars

    user_id = user["id"]
    # Filter→str-cast keeps pyright happy: `e.get("guild_name")` is `Any | None`
    # and a comprehension `if` doesn't narrow the type through the set→list.
    guild_list: list[str] = sorted({str(e["guild_name"]) for e in encounters if e.get("guild_name")})
    officer_results = await asyncio.gather(*(_officer_chars(user_id, g) for g in guild_list))
    officer_of = {g for g, chars in zip(guild_list, officer_results, strict=True) if chars}

    out: dict[int, ParsePermissions] = {}
    for e in encounters:
        gname = e.get("guild_name")
        is_uploader = _uploader_discord_id(e.get("source_dsn")) == user_id
        out[e["id"]] = ParsePermissions(
            can_delete=is_uploader or (gname in officer_of),
        )
    return out


@router.get("/parses", response_model=ParsesListResponse)
@limiter.limit("30/minute")
async def list_parses(
    request: Request,
    limit: int = 200,
    zone: str | None = None,
    size: str | None = None,
    search: str | None = None,
) -> ParsesListResponse:
    _require_user(request)

    # `limit` is now a FIGHT cap, not an upload cap. Clamp to 500 — the
    # whole page is rendered client-side; bigger pages stall the browser
    # before they stall the server.
    limit = max(1, min(limit, PARSE_LIST_MAX_LIMIT))

    # Unknown `size` value is silently dropped (no filter applied) — same
    # forgiving behaviour as the recipes route's bench filter.
    if size and size not in SIZE_BUCKETS:
        size = None

    # Inner SQL cap: generous enough that even a worst-case 24-mirror raid
    # would yield well over `limit` fights after grouping. 30x is the magic
    # number — for limit=500, inner=15000 uploads covers 625 fights at the
    # 24-mirror worst case, or 15000 unique fights at one-upload-per-fight.
    inner_cap = max(limit * PARSE_INNER_CAP_MULTIPLIER, PARSE_INNER_CAP_FLOOR)

    # Capture the request's active world OUTSIDE the threadpool closure
    # below. Even with run_sync's contextvar propagation (fixed
    # 2026-05-31), capturing here explicitly is clearer and protects
    # against future runtime/executor changes that might not propagate
    # context — defence in depth.
    active_world = current_world()

    def _list_and_group_sync() -> tuple[list[dict], list[dict], int]:
        """Run the inner-list SQL, then group into fights.

        ``_list_encounters_sync`` opens its own connection for the row
        SELECT and closes it. Afterwards this wrapper opens a SECOND
        connection that the grouper uses for its per-pair top-N lookups
        — keeping the two scopes independent means a test that mocks
        only ``_list_encounters_sync`` still produces a fresh grouper
        connection (and lets unit tests that fake the top-N helpers
        skip the SQL path entirely). Wrapping both steps in one
        ``run_sync`` keeps the route handler synchronous-DB-step-free.

        Future micro-optimisation: thread a single conn through both
        steps. Not done here — the connection cost in WAL mode is
        sub-millisecond per open, and the API split keeps the test
        seams clean.

        Phase 4 lazy backfill: any encounter inserted before the pet-
        detection pipeline shipped has is_player=NULL on its
        combatants. Classify before the merger runs so its top-N gate
        sees the correct flag. Also re-query player_count for each
        backfilled encounter so the response carries the correct
        number on the same request (no stale-on-first-load glitch)."""
        rows = _list_encounters_sync(inner_cap, zone, size, active_world, search)
        if not rows:
            return rows, [], 0
        conn = parses_db.init_db(parses_db.DB_PATH)
        try:
            # Phase 4 lazy backfill: any encounter inserted before the
            # pet-detection pipeline shipped has is_player=NULL on its
            # combatants. Classify before the merger runs so its top-N
            # gate sees the correct flag. Also re-query player_count for
            # each backfilled encounter so the response carries the
            # correct number on the same request (no stale-on-first-load
            # glitch).
            for r in rows:
                if _ensure_classified(conn, r["id"], r.get("zone")):
                    refreshed = conn.execute(
                        "SELECT COUNT(*) FROM combatants WHERE encounter_id = ? AND is_player = 1",
                        (r["id"],),
                    ).fetchone()
                    r["player_count"] = int(refreshed[0])
            fights = _group_into_fights(rows, conn)
        finally:
            conn.close()
        return rows, fights, len(fights)

    encounters, fights, total_fights = await run_sync(_list_and_group_sync)
    fights = fights[:limit]

    # Permission compute needs the flat upload list (perms are per-upload,
    # not per-fight) because trash buttons on the expanded uploader rows
    # need their own per-row can_delete.
    all_uploads_in_view: list[dict] = [u for f in fights for u in f["uploads"]]
    permissions = await _compute_permissions(request, all_uploads_in_view)

    # Batch-resolve Discord display names for every unique uploader in the
    # current view (one DB query for the whole page, no N+1). The
    # canonical-upload fight rows live in ``fights``; the per-uploader
    # rows live in ``all_uploads_in_view``. Both can carry plugin
    # source_dsns so feed both into the unique-ID set.
    uploader_ids = {
        did
        for source in (all_uploads_in_view, fights)
        for row in source
        if (did := _uploader_discord_id(row.get("source_dsn"))) is not None
    }
    uploader_names = await users_db.get_display_names_for_discord_ids(list(uploader_ids))

    def _upload_summary(u: dict) -> ParseUploadSummary:
        did = _uploader_discord_id(u.get("source_dsn"))
        return ParseUploadSummary(
            id=u["id"],
            uploaded_by=u.get("uploaded_by") or "local",
            uploader_discord_id=did,
            uploader_display_name=uploader_names.get(did) if did else None,
            started_at=u["started_at"],
            duration_s=u["duration_s"],
            total_damage=u["total_damage"],
            encdps=u["encdps"],
            success_level=u.get("success_level", 0) or 0,
            permissions=permissions.get(u["id"], ParsePermissions()),
        )

    def _encounter_summary(f: dict) -> ParseEncounterSummary:
        did = _uploader_discord_id(f.get("source_dsn"))
        return ParseEncounterSummary(
            id=f["id"],
            act_encid=f["act_encid"],
            title=f["title"],
            zone=f["zone"],
            started_at=f["started_at"],
            ended_at=f["ended_at"],
            duration_s=f["duration_s"],
            total_damage=f["total_damage"],
            encdps=f["encdps"],
            kills=f["kills"],
            deaths=f["deaths"],
            success_level=f.get("success_level", 0) or 0,
            combatant_count=f.get("combatant_count", 0),
            player_count=f.get("player_count", 0),
            category=_classify_zone(f.get("zone")),
            uploaded_by=f.get("uploaded_by") or "local",
            uploader_discord_id=did,
            uploader_display_name=uploader_names.get(did) if did else None,
            guild_name=f.get("guild_name"),
            permissions=permissions.get(f["id"], ParsePermissions()),
            uploads=[_upload_summary(u) for u in f["uploads"]],
        )

    results = [_encounter_summary(f) for f in fights]
    return ParsesListResponse(results=results, total=total_fights)


@router.get("/parses/{encounter_id}", response_model=ParseDetailResponse)
@limiter.limit("60/minute")
async def get_parse(
    request: Request,
    encounter_id: int,
    top_attacks: int = 15,
) -> ParseDetailResponse:
    _require_user(request)

    top_attacks = max(1, min(top_attacks, 50))

    enc = await run_sync(_encounter_detail_sync, encounter_id, top_attacks, current_world())
    if enc is None:
        raise HTTPException(status_code=404, detail="Parse not found")

    # encDPS percentile colouring: rank each combatant's encDPS against their
    # class's best for this boss (class leader = 100%), and flag the all-class
    # best with a star. Empty for non-boss encounters (no matching kills).
    from backend.server.api.rankings import benchmarks_for_boss  # noqa: PLC0415 — local, avoid import cycle

    # Pass the encounter's own world so benchmarks use the same server's
    # leaderboard data, regardless of the active request context.
    enc_world = enc.get("world") or current_world()
    bench = await run_sync(benchmarks_for_boss, enc["title"], enc_world)

    def _pct(c: dict, metric: str, value_key: str) -> int | None:
        cls = c.get("cls")
        if not cls:
            return None
        best = bench[metric][0].get(cls, 0.0)
        if best <= 0:
            return None
        return min(100, round(100 * (c.get(value_key) or 0.0) / best))

    def _best_overall(c: dict, metric: str, value_key: str) -> bool:
        overall = bench[metric][1]
        return bool(c.get("cls") and overall > 0 and (c.get(value_key) or 0.0) >= overall)

    combatants = [
        CombatantSummary(
            id=c["id"],
            name=c["name"],
            ally=c["ally"],
            is_player=c["is_player"],
            level=c.get("level"),
            guild_name=c.get("guild_name"),
            cls=c.get("cls"),
            duration_s=c["duration_s"],
            damage=c["damage"],
            damage_perc=c["damage_perc"],
            dps=c["dps"],
            encdps=c["encdps"],
            dps_percentile=_pct(c, "dps", "encdps"),
            dps_best_overall=_best_overall(c, "dps", "encdps"),
            hps_percentile=_pct(c, "hps", "enchps"),
            hps_best_overall=_best_overall(c, "hps", "enchps"),
            healed=c["healed"],
            enchps=c["enchps"],
            heals=c["heals"],
            crit_heals=c["crit_heals"],
            cure_dispels=c["cure_dispels"],
            power_drain=c["power_drain"],
            power_replenish=c["power_replenish"],
            heals_taken=c["heals_taken"],
            damage_taken=c["damage_taken"],
            threat_delta=c["threat_delta"],
            deaths=c["deaths"],
            kills=c["kills"],
            crit_hits=c["crit_hits"],
            crit_dam_perc=c["crit_dam_perc"],
            top_attacks=[
                AttackSummary(
                    attack_name=a["attack_name"],
                    damage=a["damage"],
                    dps=a["dps"],
                    hits=a["hits"],
                    swings=a["swings"],
                    crit_perc=a["crit_perc"],
                    max_hit=a["max_hit"],
                )
                for a in c["top_attacks"]
            ],
            top_heals=[
                HealSummary(
                    heal_name=h["attack_name"],
                    healed=h["damage"],  # `damage` column = amount healed for swing_type=3
                    hps=h["dps"],  # `dps` column = heal-per-second for swing_type=3
                    hits=h["hits"],
                    swings=h["swings"],
                    crit_perc=h["crit_perc"],
                    max_hit=h["max_hit"],
                    heal_type=h["resist"],
                )
                for h in c["top_heals"]
            ],
            top_cures=[
                CureSummary(
                    cure_name=cu["attack_name"],
                    effects_removed=cu["damage"],
                    times_cast=cu["hits"],
                    max_at_once=cu["max_hit"],
                )
                for cu in c["top_cures"]
            ],
            top_threats=[
                ThreatSummary(
                    ability_name=t["attack_name"],
                    value=t["damage"],
                    procs=t["hits"],
                    max_proc=t["max_hit"],
                    kind=t["resist"],
                )
                for t in c["top_threats"]
            ],
            damage_types=[
                DamageTypeBreakdown(
                    damage_type=d["damage_type"],
                    damage=d["damage"],
                    dps=d["dps"],
                    hits=d["hits"],
                    swings=d["swings"],
                    max_hit=d["max_hit"],
                    crit_perc=d["crit_perc"],
                )
                for d in c["damage_types"]
            ],
        )
        for c in enc["combatants"]
    ]
    # Resolve the uploader's Discord identity for badge rendering on the
    # detail page. Single-row lookup since this is one parse — the list
    # endpoint batches across the whole page.
    uploader_discord_id = _uploader_discord_id(enc.get("source_dsn"))
    uploader_display_name: str | None = None
    if uploader_discord_id:
        name_map = await users_db.get_display_names_for_discord_ids([uploader_discord_id])
        uploader_display_name = name_map.get(uploader_discord_id)

    return ParseDetailResponse(
        id=enc["id"],
        act_encid=enc["act_encid"],
        title=enc["title"],
        zone=enc["zone"],
        started_at=enc["started_at"],
        ended_at=enc["ended_at"],
        duration_s=enc["duration_s"],
        total_damage=enc["total_damage"],
        encdps=enc["encdps"],
        kills=enc["kills"],
        deaths=enc["deaths"],
        success_level=enc.get("success_level", 0) or 0,
        uploaded_by=enc.get("uploaded_by") or "local",
        uploader_discord_id=uploader_discord_id,
        uploader_display_name=uploader_display_name,
        hidden=bool(enc.get("hidden_at")),
        combatants=combatants,
    )
