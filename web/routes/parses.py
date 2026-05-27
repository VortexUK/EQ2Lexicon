"""
GET /api/parses          — paginated list of recent encounters.
GET /api/parses/{id}     — encounter detail with combatants + top attacks each.

Reads from the local `data/parses/parses.db` populated by `parses.ingest`.
Sync DB helpers from `parses.db` are dispatched to a thread via
run_in_executor — same pattern as web/routes/recipes.py.

Auth: any authenticated session can read. Officer-only / guild-scoped
filtering is a Phase 3 concern (when uploads are added).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import sqlite3
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from census.client import CensusClient
from parses import db as parses_db
from parses.boss import is_boss
from parses.models import (
    AttackType,
    Combatant,
    CombatantSnapshot,
    DamageType,
    Encounter,
    _to_bool_tf,
    _to_float,
    _to_int,
    _to_perc,
    _to_str_or_none,
    _to_ts,
)
from web.auth_deps import (
    is_admin as _is_admin,
)
from web.auth_deps import (
    require_user_session as _require_user,
)
from web.auth_deps import (
    require_user_session_or_token,
)
from web.cache import character_cache
from web.config import ALLOWED_SERVERS as _ALLOWED_SERVERS
from web.config import SERVICE_ID as _SERVICE_ID
from web.config import WORLD as _WORLD
from web.limiter import limiter
from web.server_context import current_world

# Pre-lowered comparison set so each ingest doesn't redo the work.
# Computed at module import — env changes need a process restart, same
# as ADMIN_DISCORD_IDS. ALLOWED_SERVERS itself stays in its original
# casing for display in /auth/whoami responses.
_ALLOWED_SERVERS_LOWER: frozenset[str] = frozenset(s.lower() for s in _ALLOWED_SERVERS)

_log = logging.getLogger(__name__)

router = APIRouter(tags=["parses"])


def _uploader_discord_id(source_dsn: str | None) -> str | None:
    """At ingest, plugin uploads stamp source_dsn as 'plugin:<discord_id>'.
    Returns the discord ID for plugin-uploaded rows, None for local ingests
    or malformed values."""
    if not source_dsn or not source_dsn.startswith("plugin:"):
        return None
    return source_dsn[len("plugin:") :] or None


# EQ2 server names are a small known set with a predictable shape —
# letters, digits, spaces, apostrophes, hyphens. Match conservatively
# and fall back to EQ2_WORLD when the plugin sends garbage. Caps at
# 30 chars to match the Pydantic max_length on logger_server.
_VALID_WORLD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 '_-]{0,30}$")

# EQ2 character names are letters only, max 15 characters per
# Daybreak's naming rules. Validate on ingest as defence-in-depth
# on top of the Pydantic max_length=64 cap — a hostile logger_name
# containing ':' would otherwise collide character_cache entries
# (the keys are shaped `name.lower():world.lower()` throughout the
# app). Constraining to the real EQ2 shape also keeps weird
# payloads out of Census API URLs and the parses DB.
_VALID_CHARACTER_NAME_RE = re.compile(r"^[A-Za-z]{1,15}$")


def _sanitize_world(world: str | None) -> str | None:
    """Return the world name if it matches the conservative shape we
    expect for an EQ2 server, else None so the caller falls back to
    the EQ2_WORLD env-var default. Defence-in-depth on top of the
    Pydantic max_length=64 cap — keeps obvious injection shapes
    (paths, query strings, control chars) out of Census API calls."""
    if not world:
        return None
    candidate = world.strip()
    if not candidate:
        return None
    return candidate if _VALID_WORLD_RE.match(candidate) else None


async def _resolve_uploader_guild_async(
    uploader: str,
    world: str | None = None,
) -> str | None:
    """Cache-aware guild lookup for the upload path. Order of attempts:

      1. character_cache hit on the uploader's character → return its
         guild_name (zero Census traffic).
      2. Miss → single-character Census call via get_character_guild_name
         to learn the guild name for this upload.
      3. If we learned a guild, fire-and-forget _fetch_and_cache_guild()
         to pull the full roster into character_cache so the rest of the
         raid hits step 1. Thundering-herd guard inside the helper
         dedupes concurrent prewarms for the same guild.

    ``world`` overrides the EQ2_WORLD env-var default — the plugin
    (v0.1.10+) detects the server from its log file path and stamps it
    on each upload. Empty/None → fall back to the configured default
    so older plugin versions and the local-ingest path keep working.
    Sanitised via _sanitize_world; anything that doesn't match the
    expected shape also falls back rather than feeding garbage into
    a Census API URL.

    Returns None for: uploader='local', Census error, character not found,
    or character is unguilded — callers store guild_name as NULL in all
    those cases.
    """
    if not uploader or uploader == "local":
        return None

    effective_world = _sanitize_world(world) or _WORLD
    world_lower = effective_world.lower()
    # Delimiter stays as ":" to keep cache keys compatible with the
    # rest of the app (characters.py, character.py, guild.py prewarm,
    # etc.). Collision-via-world is blocked by _sanitize_world above;
    # collision-via-name is blocked by the EQ2-name regex applied to
    # logger_name on the way in to the ingest route.
    cache_key = f"{uploader.lower()}:{world_lower}"
    cached, _ = character_cache.get_stale(cache_key)
    if cached is not None:
        return getattr(cached, "guild_name", None) or None

    client = CensusClient(service_id=_SERVICE_ID)
    try:
        guild_name = await client.get_character_guild_name(uploader, effective_world)
    except Exception as exc:
        _log.warning("Census guild lookup failed for %r: %s", uploader, exc)
        return None
    finally:
        await client.close()

    if not guild_name:
        return None

    # Background full-guild fetch — populates character_cache for every
    # member, so subsequent raid uploads from the same guild are
    # zero-Census. We don't await it; the encounter ingest can proceed
    # while the roster pre-warm runs.
    asyncio.create_task(_prewarm_guild_silently(guild_name))
    return guild_name


async def _prewarm_guild_silently(guild_name: str) -> None:
    """Background roster pre-warm used by _resolve_uploader_guild_async.
    Imports lazily to dodge the web.routes.guild ↔ web.routes.parses
    circular dependency, and never raises — pre-warm failure must not
    affect ingest success."""
    try:
        from web.routes.guild import _fetch_and_cache_guild

        await _fetch_and_cache_guild(guild_name)
    except Exception as exc:
        _log.debug("Background guild prewarm failed for %s: %s", guild_name, exc)


async def _resolve_combatant_snapshots(
    names: list[str],
    world: str | None = None,
) -> dict[str, CombatantSnapshot]:
    """Freeze each named player's identity (level / guild / class) at ingest
    time, reusing the website's character_cache.

    Per-name strategy, in order:
      1. character_cache hit → snapshot it (zero Census traffic).
      2. Miss → one Census call (get_character_guild_name) to find the
         character's guild, then *await* a full roster fetch which caches
         every guildmate. Re-check the cache for this character.

    Because a raid is overwhelmingly one guild, the first miss warms the
    whole roster, so every subsequent name is a step-1 hit — one guild
    fetch covers the parse. Unguilded players / pugs / Census errors leave
    that name absent from the result (combatant row stores NULLs).

    Never raises — snapshot resolution is best-effort and must not block a
    valid upload.
    """
    # Same sanitisation as _resolve_uploader_guild_async — a malformed
    # logger_server can't end up in a Census URL.
    effective_world = _sanitize_world(world) or _WORLD
    world_lower = effective_world.lower()
    out: dict[str, CombatantSnapshot] = {}
    client: CensusClient | None = None
    try:
        for name in names:
            cache_key = f"{name.lower()}:{world_lower}"
            cached, _ = character_cache.get_stale(cache_key)
            if cached is None:
                if client is None:
                    client = CensusClient(service_id=_SERVICE_ID)
                try:
                    guild_name = await client.get_character_guild_name(name, effective_world)
                except Exception as exc:
                    _log.warning("Combatant guild lookup failed for %r: %s", name, exc)
                    guild_name = None
                if guild_name:
                    # Awaited (not fire-and-forget) so the roster is warm for
                    # the remaining names. The thundering-herd guard in
                    # _fetch_and_cache_guild dedupes against the uploader's
                    # own prewarm for the same guild.
                    await _prewarm_guild_silently(guild_name)
                    cached, _ = character_cache.get_stale(cache_key)
            # The guild-roster resolve sometimes returns a member's class/level
            # but no equipment, so the cached ilvl is None even for a real
            # player. A direct character fetch returns equipment — fill the ilvl
            # so they aren't missing it on the leaderboard. Bounded: only fires
            # for resolved players still lacking an ilvl.
            if cached is not None and getattr(cached, "cls", None) and getattr(cached, "ilvl", None) is None:
                if client is None:
                    client = CensusClient(service_id=_SERVICE_ID)
                try:
                    char = await client.get_character(name, effective_world)
                except Exception as exc:
                    _log.warning("Combatant ilvl backfill failed for %r: %s", name, exc)
                    char = None
                if char is not None:
                    from web.routes.character import (
                        _build_char_response,  # noqa: PLC0415 — local, avoid circular import
                    )

                    resp = _build_char_response(char)
                    character_cache.set(cache_key, resp)
                    cached = resp
            if cached is not None:
                out[name] = CombatantSnapshot(
                    level=getattr(cached, "level", None),
                    guild_name=getattr(cached, "guild_name", None),
                    cls=getattr(cached, "cls", None),
                    ilvl=getattr(cached, "ilvl", None),
                )
    finally:
        if client is not None:
            await client.close()
    return out


def _cached_snapshots(names: list[str], world: str | None = None) -> dict[str, CombatantSnapshot]:
    """Cache-only snapshot lookup for the ingest response path — NO Census
    calls, so an upload can never block/time out on Census. Whatever is already
    warm in character_cache is snapshotted now; the rest is filled in by the
    background task."""
    effective_world = _sanitize_world(world) or _WORLD
    world_lower = effective_world.lower()
    out: dict[str, CombatantSnapshot] = {}
    for name in names:
        cached, _ = character_cache.get_stale(f"{name.lower()}:{world_lower}")
        if cached is not None:
            out[name] = CombatantSnapshot(
                level=getattr(cached, "level", None),
                guild_name=getattr(cached, "guild_name", None),
                cls=getattr(cached, "cls", None),
                ilvl=getattr(cached, "ilvl", None),
            )
    return out


def _update_snapshots_sync(encounter_id: int, snapshots: dict[str, CombatantSnapshot]) -> None:
    conn = parses_db.init_db(parses_db.DB_PATH)
    try:
        parses_db.update_combatant_snapshots(conn, encounter_id, snapshots)
    finally:
        conn.close()


async def _resolve_and_update_snapshots(encounter_id: int, player_names: list[str], world: str | None) -> None:
    """Background: do the full (Census-backed) snapshot resolution OFF the
    response path, then write the results onto the combatant rows. Never
    raises — best-effort enrichment."""
    try:
        snapshots = await _resolve_combatant_snapshots(player_names, world)
        if not snapshots:
            return
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _update_snapshots_sync, encounter_id, snapshots)
    except Exception as exc:
        _log.warning("Background snapshot resolution failed for encounter %s: %s", encounter_id, exc)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ParsePermissions(BaseModel):
    """Per-row flags so the UI can render delete buttons only when allowed.
    Computed against the logged-in session: admin gets all true, officer of
    the row's guild gets can_delete=true, original uploader gets it for their
    own rows."""

    can_delete: bool = False


class ParseUploadSummary(BaseModel):
    """One raider's submission within a mirror group. Smaller than
    ParseEncounterSummary — just the fields the expansion UI on /parses
    actually needs (per-uploader link, duration, damage, dps, deletion
    rights)."""

    id: int
    uploaded_by: str
    started_at: int
    duration_s: int
    total_damage: int
    encdps: float
    success_level: int
    permissions: ParsePermissions = ParsePermissions()


class ParseEncounterSummary(BaseModel):
    """One FIGHT. Top-level fields are from the canonical upload (the
    raider whose ACT captured the longest duration); `uploads` holds every
    raider's view of the same fight. Mirror grouping is by
    (guild_name, title, started_at within ±MIRROR_WINDOW_S) and only ever
    merges uploads from *distinct* uploaders."""

    id: int
    act_encid: str
    title: str
    zone: str | None
    started_at: int  # unix seconds, UTC
    ended_at: int
    duration_s: int
    total_damage: int
    encdps: float
    kills: int
    deaths: int
    success_level: int  # ACT enum: 0=unknown, 1=win, 2=loss, 3=mixed
    combatant_count: int
    player_count: int  # ally combatants with single-word names, excluding 'Unknown'
    uploaded_by: str  # who ingested the canonical upload; 'local' for local-only era
    guild_name: str | None  # stamped at ingest time from uploader's Census guild
    permissions: ParsePermissions = ParsePermissions()
    uploads: list[ParseUploadSummary] = []  # always at least 1 (the canonical itself)


class ParsesListResponse(BaseModel):
    results: list[ParseEncounterSummary]
    total: int  # total number of FIGHTS matching the filter (pre-limit)


# Two upload rows are treated as the same fight when their guild + title
# match and their start times are within this window. Kept identical to
# the frontend's previous client-side rule (was `MIRROR_WINDOW_S` in
# ParsesPage.tsx) so display behaviour doesn't change.
MIRROR_WINDOW_S = 60


class AttackSummary(BaseModel):
    attack_name: str
    damage: int
    hits: int
    swings: int
    crit_perc: float
    max_hit: int


class HealSummary(BaseModel):
    """Per-ability heal rollup. ACT writes heals into attacktype_table at
    swing_type=3; the `damage` column there is the amount healed, and
    `resist` distinguishes regular heals ('Hitpoints') from wards
    ('Absorption')."""

    heal_name: str
    healed: int
    hits: int
    swings: int
    crit_perc: float
    max_hit: int
    heal_type: str | None  # 'Hitpoints' (regular heal) or 'Absorption' (ward)


class CureSummary(BaseModel):
    """Cure events (swing_type=20). `effects_removed` is the count of
    detrimental effects cleared (ACT writes this into the `damage` column);
    `times_cast` is hit count."""

    cure_name: str
    effects_removed: int
    times_cast: int
    max_at_once: int


class ThreatSummary(BaseModel):
    """Threat / buff proc (swing_type=100, type != 'All'). For threat
    procs `value` is the threat amount; `procs` is how many times it fired."""

    ability_name: str
    value: int
    procs: int
    max_proc: int
    kind: str | None  # ACT's `resist` column — 'Increase' for threat procs


class DamageTypeBreakdown(BaseModel):
    damage_type: str
    damage: int
    dps: float
    hits: int
    swings: int
    max_hit: int
    crit_perc: float


class CombatantSummary(BaseModel):
    id: int
    name: str
    ally: bool
    # Identity frozen at ingest time (resolved from character_cache). NULL for
    # pets/NPCs, unresolved players, and parses ingested before this existed —
    # the frontend falls back to the live /api/characters/lookup for those.
    level: int | None = None
    guild_name: str | None = None
    cls: str | None = None
    duration_s: int
    damage: int
    damage_perc: float
    dps: float
    encdps: float
    # encDPS/encHPS ranked against this class's best for this boss (class leader
    # = 100), for percentile colouring on the parse page; None for non-players /
    # unresolved characters / no data. *_best_overall flags the all-class best.
    dps_percentile: int | None = None
    dps_best_overall: bool = False
    hps_percentile: int | None = None
    hps_best_overall: bool = False
    healed: int
    enchps: float
    heals: int
    crit_heals: int
    cure_dispels: int
    power_drain: int
    power_replenish: int
    heals_taken: int
    damage_taken: int
    threat_delta: int
    deaths: int
    kills: int
    crit_hits: int
    crit_dam_perc: float
    top_attacks: list[AttackSummary]
    top_heals: list[HealSummary]
    top_cures: list[CureSummary]
    top_threats: list[ThreatSummary]
    damage_types: list[DamageTypeBreakdown]


class ParseDetailResponse(BaseModel):
    id: int
    act_encid: str
    title: str
    zone: str | None
    started_at: int
    ended_at: int
    duration_s: int
    total_damage: int
    encdps: float
    kills: int
    deaths: int
    success_level: int  # ACT enum: 0=unknown, 1=win, 2=loss, 3=mixed
    hidden: bool = False  # True when the parse is soft-deleted (still openable via a ranking link)
    combatants: list[CombatantSummary]


# ---------------------------------------------------------------------------
# Sync query helpers (run via run_in_executor)
# ---------------------------------------------------------------------------


# Encounter "size" buckets — mapped to a (min_players, max_players) range
# inclusive on both ends. Used to filter the list endpoint via ?size=...
SIZE_BUCKETS: dict[str, tuple[int, int]] = {
    "individual": (1, 1),
    "group": (2, 6),
    "raid12": (7, 12),
    "raid24": (13, 24),
}

# Player detection: ally combatants whose name is one word and isn't the
# 'Unknown' fallback row ACT writes for un-attributed damage. Pets nearly
# always either consolidate into the owner or have multi-word descriptive
# names, so this catches real player count without false positives.
_PLAYER_COUNT_SQL = (
    "SELECT COUNT(*) FROM combatants c "
    "WHERE c.encounter_id = e.id "
    "  AND c.ally = 1 "
    "  AND c.name != '' "
    "  AND c.name != 'Unknown' "
    "  AND instr(c.name, ' ') = 0"
)


def _list_encounters_sync(
    inner_cap: int,
    zone: str | None,
    size: str | None,
    world: str = "Varsoon",
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
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    list_sql = f"""
        SELECT * FROM (
            SELECT e.*,
                ({_PLAYER_COUNT_SQL}) AS player_count,
                (SELECT COUNT(*) FROM combatants c2 WHERE c2.encounter_id = e.id) AS combatant_count
            FROM encounters e
        )
        {where_sql}
        ORDER BY started_at DESC
        LIMIT ?
    """

    conn = parses_db.init_db(parses_db.DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(list_sql, [*params, inner_cap]).fetchall()]
    finally:
        conn.close()


def _group_into_fights(encounters: list[dict]) -> list[dict]:
    """Greedy mirror-grouping. Two uploads are the same fight when they come
    from *different* uploaders, their guild + title match, and any pair of
    start times falls within ``MIRROR_WINDOW_S``. Same-uploader uploads are
    never merged — one raider can't mirror their own fight, so two of their
    uploads are two real fights. The canonical upload (carried as the top-level
    fields on the returned dict) is the longest-duration upload in the
    group — the raider whose ACT captured the most fight time.

    Each returned group dict looks like::

        {
            # ...all fields of the canonical upload row...
            "uploads": [<every upload dict, including the canonical>],
        }

    Stable behaviour: the previous client-side ``detectMirrors`` in
    ParsesPage used the same rule; this is a faithful Python port."""
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
            if not any(abs(u["started_at"] - e["started_at"]) <= MIRROR_WINDOW_S for u in g["uploads"]):
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
        enc_row = conn.execute("SELECT * FROM encounters WHERE id = ? AND world = ?", (encounter_id, world)).fetchone()
        if enc_row is None:
            return None
        enc = dict(enc_row)

        combatants = parses_db.get_combatants_for_encounter(conn, enc["id"])
        for c in combatants:
            c["top_attacks"] = parses_db.get_top_attacks_for_combatant(conn, c["id"], limit=top_attacks_per_combatant)
            c["top_heals"] = parses_db.get_top_heals_for_combatant(conn, c["id"], limit=top_attacks_per_combatant)
            c["top_cures"] = parses_db.get_top_cures_for_combatant(conn, c["id"], limit=top_attacks_per_combatant)
            c["top_threats"] = parses_db.get_top_threats_for_combatant(conn, c["id"], limit=top_attacks_per_combatant)
            c["damage_types"] = parses_db.get_damage_types_for_combatant(conn, c["id"])
            c["ally"] = bool(c["ally"])
        enc["combatants"] = combatants
        return enc
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


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
    from web.routes.guild import _officer_chars

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
) -> ParsesListResponse:
    _require_user(request)

    # `limit` is now a FIGHT cap, not an upload cap. Clamp to 500 — the
    # whole page is rendered client-side; bigger pages stall the browser
    # before they stall the server.
    limit = max(1, min(limit, 500))

    # Unknown `size` value is silently dropped (no filter applied) — same
    # forgiving behaviour as the recipes route's bench filter.
    if size and size not in SIZE_BUCKETS:
        size = None

    # Inner SQL cap: generous enough that even a worst-case 24-mirror raid
    # would yield well over `limit` fights after grouping. 30x is the magic
    # number — for limit=500, inner=15000 uploads covers 625 fights at the
    # 24-mirror worst case, or 15000 unique fights at one-upload-per-fight.
    inner_cap = max(limit * 30, 2000)

    loop = asyncio.get_event_loop()
    encounters = await loop.run_in_executor(None, _list_encounters_sync, inner_cap, zone, size, current_world())

    # Group uploads into fights, then apply the user-facing limit to the
    # FIGHT list. `total` reports total fights (pre-limit) so the UI can
    # surface "showing X of Y" if it ever wants to.
    fights = _group_into_fights(encounters)
    total_fights = len(fights)
    fights = fights[:limit]

    # Permission compute needs the flat upload list (perms are per-upload,
    # not per-fight) because trash buttons on the expanded uploader rows
    # need their own per-row can_delete.
    all_uploads_in_view: list[dict] = [u for f in fights for u in f["uploads"]]
    permissions = await _compute_permissions(request, all_uploads_in_view)

    def _upload_summary(u: dict) -> ParseUploadSummary:
        return ParseUploadSummary(
            id=u["id"],
            uploaded_by=u.get("uploaded_by") or "local",
            started_at=u["started_at"],
            duration_s=u["duration_s"],
            total_damage=u["total_damage"],
            encdps=u["encdps"],
            success_level=u.get("success_level", 0) or 0,
            permissions=permissions.get(u["id"], ParsePermissions()),
        )

    results = [
        ParseEncounterSummary(
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
            uploaded_by=f.get("uploaded_by") or "local",
            guild_name=f.get("guild_name"),
            permissions=permissions.get(f["id"], ParsePermissions()),
            uploads=[_upload_summary(u) for u in f["uploads"]],
        )
        for f in fights
    ]
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

    loop = asyncio.get_event_loop()
    enc = await loop.run_in_executor(None, _encounter_detail_sync, encounter_id, top_attacks, current_world())
    if enc is None:
        raise HTTPException(status_code=404, detail="Parse not found")

    # encDPS percentile colouring: rank each combatant's encDPS against their
    # class's best for this boss (class leader = 100%), and flag the all-class
    # best with a star. Empty for non-boss encounters (no matching kills).
    from web.routes.rankings import benchmarks_for_boss  # noqa: PLC0415 — local, avoid import cycle

    # Pass the encounter's own world so benchmarks use the same server's
    # leaderboard data, regardless of the active request context.
    enc_world = enc.get("world") or current_world()
    bench = await loop.run_in_executor(None, benchmarks_for_boss, enc["title"], enc_world)

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
        hidden=bool(enc.get("hidden_at")),
        combatants=combatants,
    )


# ---------------------------------------------------------------------------
# POST /api/parses/ingest — upload endpoint for the ACT plugin
# ---------------------------------------------------------------------------
#
# Accepts an ACT-shaped payload: the same row dicts ACT writes to its ODBC
# tables (encounter_table / combatant_table / damagetype_table /
# attacktype_table). Plugin sends the *raw* ACT values; transformation to
# our normalised parses.db schema happens server-side, reusing the same
# coercion helpers (_to_int, _to_perc, _to_bool_tf, etc.) that the local
# `parses.ingest` uses for direct-from-SQLite reads.
#
# `logger_name` is taken straight from the plugin (which reads
# ActGlobals.charName), so it's authoritative — no need to guess from the
# combatant table. Guild is resolved server-side via Census so the user
# can't spoof it.


class IngestEncounter(BaseModel):
    encid: str = Field(min_length=1, max_length=16)
    title: str
    zone: str | None = None
    starttime: str
    endtime: str
    duration: int = 0
    damage: int = 0
    encdps: float = 0
    kills: int = 0
    deaths: int = 0
    # ACT's GetEncounterSuccessLevel(): 0=unknown, 1=win, 2=loss, 3=mixed.
    success: int = 0


class IngestRequest(BaseModel):
    """ACT-shaped upload payload. dict[str, Any] used for combatants/damage_
    types/attack_types so the plugin can pass through raw ACT row dicts
    without us having to mirror every column in Pydantic — the column names
    are documented in parses/act_reader.py."""

    logger_name: str = Field(min_length=1, max_length=64)
    # EQ2 server the upload came from (Varsoon, Kaladim, Butcherblock,
    # …). Plugin v0.1.10+ detects this from the log file's parent
    # directory and stamps it on every upload; older versions and the
    # local-ingest path omit it and the route falls back to EQ2_WORLD.
    # Optional so older plugins keep working through the rollout.
    logger_server: str | None = Field(default=None, max_length=64)
    encounter: IngestEncounter
    combatants: list[dict[str, Any]] = []
    damage_types: list[dict[str, Any]] = []
    attack_types: list[dict[str, Any]] = []


class IngestResponse(BaseModel):
    status: str  # 'inserted', 'revived', or 'skipped'
    encounter_id: int | None  # our internal id (None for skipped)
    act_encid: str
    combatants: int
    damage_types: int
    attack_types: int
    guild_name: str | None


# Map raw ACT row dicts to our typed dataclasses — same column-name handling
# as parses/act_reader.py. Mirrors `combatant_table.class`/`damagetype_table.
# combatant`/`attacktype_table.attacker` quirks observed against real data.


def _encounter_from_payload(p: IngestEncounter) -> Encounter | None:
    started = _to_ts(p.starttime)
    ended = _to_ts(p.endtime)
    if started is None or ended is None:
        return None
    return Encounter(
        encid=p.encid,
        title=p.title or "",
        zone=_to_str_or_none(p.zone),
        started_at=started,
        ended_at=ended,
        duration_s=_to_int(p.duration),
        total_damage=_to_int(p.damage),
        encdps=_to_float(p.encdps),
        kills=_to_int(p.kills),
        deaths=_to_int(p.deaths),
        success_level=_to_int(p.success),
    )


def _combatants_from_payload(rows: list[dict], encid: str) -> list[Combatant]:
    out: list[Combatant] = []
    for r in rows:
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        out.append(
            Combatant(
                encid=encid,
                name=name,
                ally=_to_bool_tf(r.get("ally")),
                started_at=_to_ts(r.get("starttime")),
                ended_at=_to_ts(r.get("endtime")),
                duration_s=_to_int(r.get("duration")),
                damage=_to_int(r.get("damage")),
                damage_perc=_to_perc(r.get("damageperc")),
                kills=_to_int(r.get("kills")),
                healed=_to_int(r.get("healed")),
                healed_perc=_to_perc(r.get("healedperc")),
                crit_heals=_to_int(r.get("critheals")),
                heals=_to_int(r.get("heals")),
                cure_dispels=_to_int(r.get("curedispels")),
                power_drain=_to_int(r.get("powerdrain")),
                power_replenish=_to_int(r.get("powerreplenish")),
                dps=_to_float(r.get("dps")),
                encdps=_to_float(r.get("encdps")),
                enchps=_to_float(r.get("enchps")),
                hits=_to_int(r.get("hits")),
                crit_hits=_to_int(r.get("crithits")),
                blocked=_to_int(r.get("blocked")),
                misses=_to_int(r.get("misses")),
                swings=_to_int(r.get("swings")),
                heals_taken=_to_int(r.get("healstaken")),
                damage_taken=_to_int(r.get("damagetaken")),
                deaths=_to_int(r.get("deaths")),
                to_hit=_to_float(r.get("tohit")),
                crit_dam_perc=_to_perc(r.get("critdamperc")),
                crit_heal_perc=_to_perc(r.get("crithealperc")),
                crit_types=_to_str_or_none(r.get("crittypes")),
                threat_str=_to_str_or_none(r.get("threatstr")),
                threat_delta=_to_int(r.get("threatdelta")),
            )
        )
    return out


def _damage_types_from_payload(rows: list[dict], encid: str) -> list[DamageType]:
    out: list[DamageType] = []
    for r in rows:
        combatant = str(r.get("combatant") or "").strip()
        damage_type = str(r.get("type") or "").strip()
        if not combatant or not damage_type:
            continue
        out.append(
            DamageType(
                encid=encid,
                combatant_name=combatant,
                grouping_label=_to_str_or_none(r.get("grouping")),
                damage_type=damage_type,
                started_at=_to_ts(r.get("starttime")),
                ended_at=_to_ts(r.get("endtime")),
                duration_s=_to_int(r.get("duration")),
                damage=_to_int(r.get("damage")),
                encdps=_to_float(r.get("encdps")),
                char_dps=_to_float(r.get("chardps")),
                dps=_to_float(r.get("dps")),
                average=_to_float(r.get("average")),
                median=_to_int(r.get("median")),
                min_hit=_to_int(r.get("minhit")),
                max_hit=_to_int(r.get("maxhit")),
                hits=_to_int(r.get("hits")),
                crit_hits=_to_int(r.get("crithits")),
                blocked=_to_int(r.get("blocked")),
                misses=_to_int(r.get("misses")),
                swings=_to_int(r.get("swings")),
                to_hit=_to_float(r.get("tohit")),
                average_delay=_to_float(r.get("averagedelay")),
                crit_perc=_to_perc(r.get("critperc")),
                crit_types=_to_str_or_none(r.get("crittypes")),
            )
        )
    return out


def _attack_types_from_payload(rows: list[dict], encid: str) -> list[AttackType]:
    """ACT writes per-combatant rollups as type='All' across various
    swingtypes — strip those (same rule as the file-based reader)."""
    out: list[AttackType] = []
    for r in rows:
        attacker = str(r.get("attacker") or "").strip()
        attack_name = str(r.get("type") or "").strip()
        if not attacker or not attack_name or attack_name == "All":
            continue
        out.append(
            AttackType(
                encid=encid,
                combatant_name=attacker,
                victim=_to_str_or_none(r.get("victim")),
                swing_type=_to_int(r.get("swingtype")),
                attack_name=attack_name,
                started_at=_to_ts(r.get("starttime")),
                ended_at=_to_ts(r.get("endtime")),
                duration_s=_to_int(r.get("duration")),
                damage=_to_int(r.get("damage")),
                encdps=_to_float(r.get("encdps")),
                char_dps=_to_float(r.get("chardps")),
                dps=_to_float(r.get("dps")),
                average=_to_float(r.get("average")),
                median=_to_int(r.get("median")),
                min_hit=_to_int(r.get("minhit")),
                max_hit=_to_int(r.get("maxhit")),
                resist=_to_str_or_none(r.get("resist")),
                hits=_to_int(r.get("hits")),
                crit_hits=_to_int(r.get("crithits")),
                blocked=_to_int(r.get("blocked")),
                misses=_to_int(r.get("misses")),
                swings=_to_int(r.get("swings")),
                to_hit=_to_float(r.get("tohit")),
                average_delay=_to_float(r.get("averagedelay")),
                crit_perc=_to_perc(r.get("critperc")),
                crit_types=_to_str_or_none(r.get("crittypes")),
            )
        )
    return out


def _ingest_payload_sync(
    payload: IngestRequest,
    uploaded_by: str,
    guild_name: str | None,
    source_dsn: str,
    snapshots: dict[str, CombatantSnapshot] | None = None,
    world: str = "Varsoon",
) -> tuple[str, int | None, int, int, int]:
    """Write the payload into parses.db. Returns (status, encounter_id,
    n_combatants, n_damage_types, n_attack_types).

    status: 'inserted' on success, 'revived' if the encid was already
    ingested but soft-deleted (re-upload un-hides it), 'skipped' if the
    encid was already ingested and still visible — the upload is
    idempotent on retries.

    ``world`` is the authoritative server name (on the HTTP path this is the
    allowlist-gated ``sanitized_server`` derived from logger_server) and is
    stored on the encounter row and in ingest_log so the same act_encid from
    two different servers are distinct."""
    enc = _encounter_from_payload(payload.encounter)
    if enc is None:
        raise HTTPException(status_code=400, detail="Encounter starttime/endtime unparseable")
    combatants = _combatants_from_payload(payload.combatants, enc.encid)
    if not combatants:
        raise HTTPException(status_code=400, detail="No combatants in payload")
    damage_types = _damage_types_from_payload(payload.damage_types, enc.encid)
    attack_types = _attack_types_from_payload(payload.attack_types, enc.encid)

    conn = parses_db.init_db(parses_db.DB_PATH)
    try:
        # Idempotency: skip if this world+encid pair was already ingested.
        if parses_db.is_ingested(conn, enc.encid, world):
            existing = parses_db.find_encounter_by_act_encid(conn, enc.encid, world)
            # A re-upload of a soft-deleted (hidden) parse should bring it back,
            # not silently skip — un-hide it so it returns to the list.
            if existing and existing.get("hidden_at") is not None:
                parses_db.unhide_encounter(conn, existing["id"])
                return ("revived", existing["id"], 0, 0, 0)
            return ("skipped", existing["id"] if existing else None, 0, 0, 0)

        ingested_at = int(time.time())
        with conn:
            encounter_id = parses_db.insert_encounter(
                conn,
                enc,
                source_dsn=source_dsn,
                ingested_at=ingested_at,
                uploaded_by=uploaded_by,
                guild_name=guild_name,
                world=world,
            )
            name_to_id = parses_db.insert_combatants_bulk(conn, encounter_id, combatants, snapshots)
            n_dt = parses_db.insert_damage_types_bulk(conn, name_to_id, damage_types)
            n_at = parses_db.insert_attack_types_bulk(conn, name_to_id, attack_types)
            parses_db.mark_ingested(
                conn,
                enc.encid,
                encounter_id,
                source_dsn=source_dsn,
                ingested_at=ingested_at,
                world=world,
            )
        return ("inserted", encounter_id, len(combatants), n_dt, n_at)
    finally:
        conn.close()


# Header name shipped by the plugin (v0.1.8+). MUST match
# PayloadSigner.SignatureHeaderName in the EQ2LexiconACTPlugin repo —
# changing one side without the other breaks HMAC validation.
PLUGIN_SIGNATURE_HEADER = "X-Lexicon-Signature"


async def _validate_payload_signature(
    request: Request,
    user: dict,
) -> None:
    """HMAC-SHA256 validation of the upload body, keyed by the bearer
    token. Plugin v0.1.8+ ships this header on every upload.

    STRICT mode (flipped from opportunistic on 2026-05-25):
      * token-auth + header missing  → 401 (force plugin update)
      * token-auth + header present  → must verify; mismatch is 401
      * session-auth + header present → 400 (confused client)
      * session-auth + header absent → allowed (browser uploads, if any)

    The strict flip means v0.1.7 and older plugins now hit a clear 401
    telling them to update. The plugin's update-awareness banner (also
    introduced in v0.1.8) makes the upgrade path obvious in the UI.

    Threat model: see PayloadSigner.cs in the plugin repo. Short version
    — this stops payload tampering in flight; it does NOT prevent the
    legitimate token holder from signing whatever JSON they want (they
    have the key). Real integrity comes from server-side sanity checks
    on top of this.
    """
    sig_header = request.headers.get(PLUGIN_SIGNATURE_HEADER)

    # Session-cookie auth doesn't have a token-style HMAC key. Skip the
    # whole validation path for browsers, but reject explicitly if a
    # session client somehow sends the header (confused client > silent
    # accept).
    if user.get("auth_source") != "token":
        if sig_header:
            raise HTTPException(
                status_code=400,
                detail=f"{PLUGIN_SIGNATURE_HEADER} is only valid for token-authenticated requests.",
            )
        return

    # Token auth from here on — header is required.
    if not sig_header:
        raise HTTPException(
            status_code=401,
            detail=(
                f"{PLUGIN_SIGNATURE_HEADER} is required for plugin uploads. "
                "Update the EQ2 Lexicon ACT plugin to v0.1.8 or later: "
                "https://github.com/VortexUK/EQ2LexiconACTPlugin/releases/latest"
            ),
        )

    auth_header = request.headers.get("authorization") or ""
    if not auth_header.lower().startswith("bearer "):
        # Defensive — require_user_session_or_token already verified
        # bearer presence on the token path. If we reach here without
        # one, something has gone very wrong upstream.
        raise HTTPException(
            status_code=401,
            detail="Missing bearer token for signature validation.",
        )
    raw_token = auth_header[len("Bearer ") :].strip()

    # Request.body() is cached after FastAPI's body-injection consumes it
    # to build `body: IngestRequest`, so re-reading here is free.
    body_bytes = await request.body()
    expected = hmac.new(
        raw_token.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, sig_header.strip().lower()):
        raise HTTPException(
            status_code=401,
            detail=f"{PLUGIN_SIGNATURE_HEADER} does not match payload.",
        )


@router.post("/parses/ingest", response_model=IngestResponse, status_code=201)
@limiter.limit("60/minute")
async def ingest_parse(
    request: Request,
    body: IngestRequest,
    background: BackgroundTasks,
) -> IngestResponse:
    user = await require_user_session_or_token(request)
    await _validate_payload_signature(request, user)

    # Trust the plugin's logger_name (it reads ActGlobals.charName) and
    # use it as the uploader identifier on the encounter row. The session/
    # token user_id is what we'd surface for "who uploaded this" if/when
    # we add an uploader-by-user-id column in Phase 3+.
    uploader = body.logger_name.strip()
    if not uploader:
        raise HTTPException(status_code=400, detail="logger_name must not be empty")
    # EQ2 character names are letters only, 1-15 chars. Reject
    # anything else — keeps malformed payloads out of Census API
    # URLs, parses-DB rows, and prevents the ":"-injection cache-
    # collision path called out in the v0.1.13 audit (M4).
    if not _VALID_CHARACTER_NAME_RE.match(uploader):
        raise HTTPException(
            status_code=400,
            detail="logger_name must be 1-15 letters (the EQ2 character-name shape).",
        )

    # Server allowlist gate — strict mode.
    #
    # The plugin stamps logger_server from the active ACT log path
    # (v0.1.10+). Pre-v0.1.10 builds didn't send the field at all and
    # any plugin more than two minor versions behind the latest release
    # has been blocked client-side by the version gate, plus rejected
    # server-side by the X-Lexicon-Signature strict check since
    # 2026-05-25. So any payload that lands here without logger_server
    # is effectively a misconfigured client we want to surface, not a
    # legitimate request to silently fall back to EQ2_WORLD.
    #
    # Three rejection cases, ordered from most-actionable-for-user to
    # least:
    #   1. logger_server missing/empty   → 400, "update your plugin"
    #   2. logger_server malformed shape → 400, "logger_server is bad"
    #   3. logger_server not in allow set → 403, with the allowed list
    raw_server = (body.logger_server or "").strip()
    if not raw_server:
        raise HTTPException(
            status_code=400,
            detail="logger_server is required. Please update the EQ2 Lexicon ACT plugin to v0.1.14 or later.",
        )
    sanitized_server = _sanitize_world(raw_server)
    if sanitized_server is None:
        raise HTTPException(
            status_code=400,
            detail=f"logger_server '{raw_server}' is malformed.",
        )
    if sanitized_server.lower() not in _ALLOWED_SERVERS_LOWER:
        # Sort the allowed list so the error message renders
        # deterministically — same display order as /auth/whoami.
        raise HTTPException(
            status_code=403,
            detail=(
                f"Server '{sanitized_server}' is not on the allowed list. "
                f"Allowed: {', '.join(sorted(_ALLOWED_SERVERS))}."
            ),
        )

    # After the strict gate, sanitized_server is a guaranteed-valid,
    # allowlisted server name (Varsoon/Wuoshi) — and those ARE registry
    # worlds — so it is the authoritative `world` we persist this parse
    # under. The gate has already established a concrete world, so there is
    # no current_world() fallback to fall through to on this HTTP path.
    parse_world = sanitized_server

    # Cache-aware guild resolve: hits character_cache first; on miss does a
    # one-character Census call and pre-warms the full roster in the
    # background so the rest of the raid's uploads are zero-Census.
    # logger_server (plugin v0.1.10+) overrides EQ2_WORLD when present —
    # enables a Varsoon-configured deployment to correctly resolve a
    # Wuoshi upload, for instance. After the strict gate above the value is
    # guaranteed valid; _resolve_uploader_guild_async re-sanitises it
    # defensively and that fallback is now only reachable from the
    # local-ingest pipeline.
    guild_name = await _resolve_uploader_guild_async(uploader, body.logger_server)

    # Freeze each player ally's level/guild/class at ingest. Restricted to
    # player-like names (single-word ally, not the 'Unknown' rollup) so we
    # never burn Census calls on pets/NPCs that don't exist as characters.
    player_names = [
        name
        for r in body.combatants
        if _to_bool_tf(r.get("ally"))
        and (name := str(r.get("name") or "").strip())
        and " " not in name
        and name != "Unknown"
    ]
    # Cache-only on the response path — NEVER hit Census here, or a cold-cache
    # raid upload (up to N serial 30 s Census calls) would time the plugin out.
    # Whatever's already warm in character_cache is frozen now; the rest is
    # resolved by a background task that updates the combatant rows after the
    # response has already gone out.
    snapshots = _cached_snapshots(player_names, body.logger_server)

    loop = asyncio.get_event_loop()

    status, encounter_id, n_c, n_dt, n_at = await loop.run_in_executor(
        None,
        _ingest_payload_sync,
        body,
        uploader,
        guild_name,
        f"plugin:{user['id']}",  # source_dsn marks the auth path
        snapshots,
        parse_world,
    )

    # Schedule the full (Census-backed) resolution off the response path. For
    # freshly-inserted parses, and for revived ones (so the brought-back parse
    # re-resolves its players against the now-warmer cache). Skipped rows
    # already have their snapshots, and an empty name list has nothing to do.
    if status in ("inserted", "revived") and encounter_id is not None and player_names:
        background.add_task(_resolve_and_update_snapshots, encounter_id, player_names, body.logger_server)

    return IngestResponse(
        status=status,
        encounter_id=encounter_id,
        act_encid=body.encounter.encid,
        combatants=n_c,
        damage_types=n_dt,
        attack_types=n_at,
        guild_name=guild_name,
    )


# ---------------------------------------------------------------------------
# DELETE /api/parses/{encounter_id} — single encounter
# DELETE /api/parses?guild=...     — bulk by filter
#
# Permission tiers (any one is sufficient):
#   * admin (Discord ID in ADMIN_DISCORD_IDS)
#   * officer of the encounter's guild_name (via Census rank lookup)
#   * the encounter's original uploader (source_dsn = "plugin:<discord_id>")
# Cascades to combatants / damage_types / attack_types / ingest_log via the
# FK ON DELETE CASCADE on those tables.
# ---------------------------------------------------------------------------


class DeleteParsesResponse(BaseModel):
    deleted: int


async def _can_delete_encounter(user: dict, enc: dict) -> bool:
    """Authorise deletion of one encounter row (must carry `guild_name` and
    `source_dsn`). Any of: admin, the original uploader, or an officer of the
    encounter's guild. Never trusts the caller for guild/uploader — both come
    from the stored row."""
    if _is_admin(user) or _uploader_discord_id(enc.get("source_dsn")) == user["id"]:
        return True
    gname = enc.get("guild_name")
    if gname:
        from web.routes.guild import _officer_chars

        if await _officer_chars(user["id"], gname):
            return True
    return False


def _fetch_encounter_auth_rows(ids: list[int], world: str) -> list[dict]:
    """Fetch the (id, guild_name, source_dsn, title, hidden_at) rows needed to
    authorise a delete, scoped to *world* so a cross-server id returns nothing.
    Runs in an executor."""
    conn = parses_db.init_db(parses_db.DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, guild_name, source_dsn, title, hidden_at FROM encounters WHERE id IN ({placeholders}) AND world = ?",
            [*ids, world],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _apply_delete(conn: sqlite3.Connection, enc: dict, *, purge: bool, hidden_at: int) -> bool:
    """Hard-purge wins; otherwise boss kills are soft-deleted (preserve any
    ranking) and trash is hard-deleted. Caller has already authorised + (for
    purge) checked admin."""
    if purge or not is_boss(enc.get("title")):
        return parses_db.delete_encounter(conn, enc["id"])
    return parses_db.soft_delete_encounter(conn, enc["id"], hidden_at)


@router.delete("/parses/batch", response_model=DeleteParsesResponse)
@limiter.limit("30/minute")
async def delete_parses_batch(
    request: Request,
    ids: str,
    purge: bool = False,
) -> DeleteParsesResponse:
    """Delete an explicit set of encounter ids — the uploads that make up one
    multi-uploader fight on /parses. `ids` is comma-separated.

    Each id is authorised independently with the same rule as single-delete,
    so an officer of the fight's guild (or an admin) can remove EVERY raider's
    upload of the encounter in one action, while a non-privileged caller only
    removes the ids they're entitled to. Ids the caller can't delete are
    skipped rather than failing the whole request; a 403 is returned only when
    none are permitted.

    `purge=true` (admin only) hard-deletes even boss-kill encounters, removing
    them from leaderboards. Without purge, boss kills are soft-deleted
    (hidden_at set) to preserve their ranking entry.

    Defined before /parses/{encounter_id} so the literal path wins the route
    match.
    """
    user = _require_user(request)
    if purge and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Only an admin may hard-purge parses")

    id_list: list[int] = []
    for tok in ids.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            id_list.append(int(tok))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid encounter id: {tok!r}") from None
    id_list = list(dict.fromkeys(id_list))[:64]  # dedupe, cap fan-out
    if not id_list:
        raise HTTPException(status_code=400, detail="ids must not be empty")

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _fetch_encounter_auth_rows, id_list, current_world())
    if not rows:
        raise HTTPException(status_code=404, detail="No matching parses")

    allowed_rows = [enc for enc in rows if await _can_delete_encounter(user, enc)]
    if not allowed_rows:
        raise HTTPException(status_code=403, detail="Not authorised to delete these parses")

    now = int(time.time())

    def _delete_many() -> int:
        conn = parses_db.init_db(parses_db.DB_PATH)
        try:
            return sum(1 for enc in allowed_rows if _apply_delete(conn, enc, purge=purge, hidden_at=now))
        finally:
            conn.close()

    n = await loop.run_in_executor(None, _delete_many)
    return DeleteParsesResponse(deleted=n)


@router.delete("/parses/{encounter_id}", response_model=DeleteParsesResponse)
@limiter.limit("30/minute")
async def delete_parse(
    request: Request,
    encounter_id: int,
    purge: bool = False,
) -> DeleteParsesResponse:
    user = _require_user(request)
    if purge and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Only an admin may hard-purge a parse")

    # Look up the row so we can authorise against its real guild_name and
    # source_dsn — never trust the caller for either.  Also enforces
    # per-server isolation: an id from another world returns no rows → 404.
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _fetch_encounter_auth_rows, [encounter_id], current_world())
    if not rows:
        raise HTTPException(status_code=404, detail="Parse not found")

    if not await _can_delete_encounter(user, rows[0]):
        raise HTTPException(status_code=403, detail="Not authorised to delete this parse")

    enc = rows[0]
    now = int(time.time())

    def _delete_sync() -> bool:
        conn = parses_db.init_db(parses_db.DB_PATH)
        try:
            return _apply_delete(conn, enc, purge=purge, hidden_at=now)
        finally:
            conn.close()

    removed = await loop.run_in_executor(None, _delete_sync)
    return DeleteParsesResponse(deleted=1 if removed else 0)


@router.delete("/parses", response_model=DeleteParsesResponse)
@limiter.limit("10/minute")
async def delete_parses_bulk(
    request: Request,
    guild: str,
    zone: str | None = None,
    date: str | None = None,  # YYYY-MM-DD in server local timezone
    uploader: str | None = None,
    purge: bool = False,
) -> DeleteParsesResponse:
    """Bulk delete by filter. `guild` is required — there is deliberately no
    "delete everything across all guilds" path. Permission: admin or officer
    of the named guild.

    Boss kills are soft-deleted (hidden_at set, ranking entry preserved);
    trash encounters are hard-deleted. `purge=true` (admin only) hard-deletes
    everything, including boss kills."""
    user = _require_user(request)
    if purge and not _is_admin(user):
        raise HTTPException(status_code=403, detail="Only an admin may hard-purge parses")

    guild = guild.strip()
    if not guild:
        raise HTTPException(status_code=400, detail="guild parameter must not be empty")

    allowed = _is_admin(user)
    if not allowed:
        from web.routes.guild import _officer_chars

        if await _officer_chars(user["id"], guild):
            allowed = True
    if not allowed:
        raise HTTPException(status_code=403, detail="Not authorised to delete parses for this guild")

    loop = asyncio.get_event_loop()

    now = int(time.time())

    _world = current_world()

    def _delete_sync() -> int:
        conn = parses_db.init_db(parses_db.DB_PATH)
        try:
            matches = parses_db.find_encounters_by_filter(
                conn,
                guild_name=guild,
                zone=zone,
                date=date,
                uploaded_by=uploader,
                world=_world,
            )
            return sum(1 for enc in matches if _apply_delete(conn, enc, purge=purge, hidden_at=now))
        finally:
            conn.close()

    n = await loop.run_in_executor(None, _delete_sync)
    return DeleteParsesResponse(deleted=n)
