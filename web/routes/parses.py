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
import os
import sqlite3
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from parses import db as parses_db
from parses.ingest import _resolve_guild_sync
from parses.models import (
    AttackType,
    Combatant,
    DamageType,
    Encounter,
    _to_bool_tf,
    _to_float,
    _to_int,
    _to_str_or_none,
    _to_ts,
)
from web.auth_deps import require_user_session_or_token
from web.limiter import limiter

router = APIRouter(tags=["parses"])

# Admin allow-list mirrors the one in web/routes/admin.py — duplicated rather
# than imported so this route doesn't depend on the admin route module.
_ADMIN_IDS: frozenset[str] = frozenset(filter(None, os.getenv("ADMIN_DISCORD_IDS", "").split(",")))


def _is_admin(user: dict | None) -> bool:
    return bool(user and user.get("id") in _ADMIN_IDS)


def _uploader_discord_id(source_dsn: str | None) -> str | None:
    """At ingest, plugin uploads stamp source_dsn as 'plugin:<discord_id>'.
    Returns the discord ID for plugin-uploaded rows, None for local ingests
    or malformed values."""
    if not source_dsn or not source_dsn.startswith("plugin:"):
        return None
    return source_dsn[len("plugin:") :] or None


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ParsePermissions(BaseModel):
    """Per-row flags so the UI can render delete buttons only when allowed.
    Computed against the logged-in session: admin gets all true, officer of
    the row's guild gets can_delete=true, original uploader gets it for their
    own rows."""

    can_delete: bool = False


class ParseEncounterSummary(BaseModel):
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
    uploaded_by: str  # who ingested this encounter; 'local' for the local-only era
    guild_name: str | None  # stamped at ingest time from uploader's Census guild
    permissions: ParsePermissions = ParsePermissions()


class ParsesListResponse(BaseModel):
    results: list[ParseEncounterSummary]
    total: int


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
    duration_s: int
    damage: int
    damage_perc: float
    dps: float
    encdps: float
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
    combatants: list[CombatantSummary]


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _require_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


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
    limit: int,
    zone: str | None,
    size: str | None,
) -> tuple[list[dict], int]:
    """Return (encounters_with_counts, total_count) ordered started_at DESC."""
    if not parses_db.DB_PATH.exists():
        return [], 0

    # Build the encounter list (with computed player_count + combatant_count).
    where_clauses: list[str] = []
    params: list = []
    if zone:
        where_clauses.append("e.zone = ?")
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
    count_sql = f"""
        SELECT COUNT(*) FROM (
            SELECT e.id,
                ({_PLAYER_COUNT_SQL}) AS player_count
            FROM encounters e
        )
        {where_sql}
    """

    conn = parses_db.init_db()
    try:
        conn.row_factory = sqlite3.Row
        encounters = [dict(r) for r in conn.execute(list_sql, [*params, limit]).fetchall()]
        total = conn.execute(count_sql, params).fetchone()[0]
        return encounters, total
    finally:
        conn.close()


def _encounter_detail_sync(encounter_id: int, top_attacks_per_combatant: int) -> dict | None:
    """Return the encounter + its combatants + top attacks per combatant."""
    if not parses_db.DB_PATH.exists():
        return None
    conn = parses_db.init_db()
    try:
        conn.row_factory = sqlite3.Row
        enc_row = conn.execute("SELECT * FROM encounters WHERE id = ?", (encounter_id,)).fetchone()
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

    # Clamp limit so a hostile caller can't ask for millions.
    limit = max(1, min(limit, 500))

    # Unknown `size` value is silently dropped (no filter applied) — same
    # forgiving behaviour as the recipes route's bench filter.
    if size and size not in SIZE_BUCKETS:
        size = None

    loop = asyncio.get_event_loop()
    encounters, total = await loop.run_in_executor(None, _list_encounters_sync, limit, zone, size)
    permissions = await _compute_permissions(request, encounters)

    results = [
        ParseEncounterSummary(
            id=e["id"],
            act_encid=e["act_encid"],
            title=e["title"],
            zone=e["zone"],
            started_at=e["started_at"],
            ended_at=e["ended_at"],
            duration_s=e["duration_s"],
            total_damage=e["total_damage"],
            encdps=e["encdps"],
            kills=e["kills"],
            deaths=e["deaths"],
            success_level=e.get("success_level", 0) or 0,
            combatant_count=e.get("combatant_count", 0),
            player_count=e.get("player_count", 0),
            uploaded_by=e.get("uploaded_by") or "local",
            guild_name=e.get("guild_name"),
            permissions=permissions.get(e["id"], ParsePermissions()),
        )
        for e in encounters
    ]
    return ParsesListResponse(results=results, total=total)


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
    enc = await loop.run_in_executor(None, _encounter_detail_sync, encounter_id, top_attacks)
    if enc is None:
        raise HTTPException(status_code=404, detail="Parse not found")

    combatants = [
        CombatantSummary(
            id=c["id"],
            name=c["name"],
            ally=c["ally"],
            duration_s=c["duration_s"],
            damage=c["damage"],
            damage_perc=c["damage_perc"],
            dps=c["dps"],
            encdps=c["encdps"],
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
    encounter: IngestEncounter
    combatants: list[dict[str, Any]] = []
    damage_types: list[dict[str, Any]] = []
    attack_types: list[dict[str, Any]] = []


class IngestResponse(BaseModel):
    status: str  # 'inserted' or 'skipped'
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
    from parses.models import _to_perc

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
    from parses.models import _to_perc

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
    from parses.models import _to_perc

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
) -> tuple[str, int | None, int, int, int]:
    """Write the payload into parses.db. Returns (status, encounter_id,
    n_combatants, n_damage_types, n_attack_types).

    status: 'inserted' on success, 'skipped' if (act_encid, uploaded_by)
    already ingested by this user — the upload is idempotent on retries."""
    enc = _encounter_from_payload(payload.encounter)
    if enc is None:
        raise HTTPException(status_code=400, detail="Encounter starttime/endtime unparseable")
    combatants = _combatants_from_payload(payload.combatants, enc.encid)
    if not combatants:
        raise HTTPException(status_code=400, detail="No combatants in payload")
    damage_types = _damage_types_from_payload(payload.damage_types, enc.encid)
    attack_types = _attack_types_from_payload(payload.attack_types, enc.encid)

    conn = parses_db.init_db()
    try:
        # Idempotency: skip if this uploader has already ingested this encid.
        # NOTE: the current UNIQUE constraint is on act_encid alone, so a
        # different uploader's payload with the same encid will collide at
        # insert time. Phase 3+ will switch to UNIQUE(act_encid, uploaded_by).
        if parses_db.is_ingested(conn, enc.encid):
            existing = parses_db.find_encounter_by_act_encid(conn, enc.encid)
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
            )
            name_to_id = parses_db.insert_combatants_bulk(conn, encounter_id, combatants)
            n_dt = parses_db.insert_damage_types_bulk(conn, name_to_id, damage_types)
            n_at = parses_db.insert_attack_types_bulk(conn, name_to_id, attack_types)
            parses_db.mark_ingested(
                conn,
                enc.encid,
                encounter_id,
                source_dsn=source_dsn,
                ingested_at=ingested_at,
            )
        return ("inserted", encounter_id, len(combatants), n_dt, n_at)
    finally:
        conn.close()


@router.post("/parses/ingest", response_model=IngestResponse, status_code=201)
@limiter.limit("60/minute")
async def ingest_parse(
    request: Request,
    body: IngestRequest,
) -> IngestResponse:
    user = await require_user_session_or_token(request)

    # Trust the plugin's logger_name (it reads ActGlobals.charName) and
    # use it as the uploader identifier on the encounter row. The session/
    # token user_id is what we'd surface for "who uploaded this" if/when
    # we add an uploader-by-user-id column in Phase 3+.
    uploader = body.logger_name.strip()
    if not uploader:
        raise HTTPException(status_code=400, detail="logger_name must not be empty")

    # Resolve guild via Census from the logger character. Single call per
    # upload — same pattern as local ingest's _resolve_guild_sync.
    loop = asyncio.get_event_loop()
    guild_name = await loop.run_in_executor(None, _resolve_guild_sync, uploader)

    status, encounter_id, n_c, n_dt, n_at = await loop.run_in_executor(
        None,
        _ingest_payload_sync,
        body,
        uploader,
        guild_name,
        f"plugin:{user['id']}",  # source_dsn marks the auth path
    )

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


@router.delete("/parses/{encounter_id}", response_model=DeleteParsesResponse)
@limiter.limit("30/minute")
async def delete_parse(
    request: Request,
    encounter_id: int,
) -> DeleteParsesResponse:
    user = _require_user(request)

    # Look up the row so we can authorise against its real guild_name and
    # source_dsn — never trust the caller for either.
    loop = asyncio.get_event_loop()

    def _fetch_sync() -> dict | None:
        conn = parses_db.init_db()
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, guild_name, source_dsn FROM encounters WHERE id = ?",
                (encounter_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    enc = await loop.run_in_executor(None, _fetch_sync)
    if enc is None:
        raise HTTPException(status_code=404, detail="Parse not found")

    allowed = _is_admin(user) or _uploader_discord_id(enc.get("source_dsn")) == user["id"]
    if not allowed and enc.get("guild_name"):
        from web.routes.guild import _officer_chars

        if await _officer_chars(user["id"], enc["guild_name"]):
            allowed = True
    if not allowed:
        raise HTTPException(status_code=403, detail="Not authorised to delete this parse")

    def _delete_sync() -> bool:
        conn = parses_db.init_db()
        try:
            return parses_db.delete_encounter(conn, encounter_id)
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
) -> DeleteParsesResponse:
    """Bulk delete by filter. `guild` is required — there is deliberately no
    "delete everything across all guilds" path. Permission: admin or officer
    of the named guild."""
    user = _require_user(request)
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

    def _delete_sync() -> int:
        conn = parses_db.init_db()
        try:
            return parses_db.delete_encounters_by_filter(
                conn,
                guild_name=guild,
                zone=zone,
                date=date,
                uploaded_by=uploader,
            )
        finally:
            conn.close()

    n = await loop.run_in_executor(None, _delete_sync)
    return DeleteParsesResponse(deleted=n)
