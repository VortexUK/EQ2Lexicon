"""Raid-attendance endpoints — parser ingest + the guild Attendance tab.

POST /api/attendance/ingest              — EQ2Parser snapshot upload
GET  /api/guild/{g}/attendance           — session list (member-gated)
GET  /api/guild/{g}/attendance/{id}      — session detail with categories
DELETE /api/guild/{g}/attendance/{id}    — officer moderation

Ingest reuses the parses upload contract (bearer token + HMAC over the
uncompressed JSON; gzip handled by middleware) and resolves + verifies the
uploader's guild server-side from logger_name — the client never asserts its
own guild. Categories (present / sat_out / afk / awol) are derived at read
time by backend/server/attendance.py against the raid-planner roles, claims,
and the availability calendar.
"""

from __future__ import annotations

import json
import logging
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.server import attendance as derive
from backend.server import raid_live
from backend.server.api.guild import _validate_guild_name
from backend.server.api.parses.ingest import (
    _ALLOWED_SERVERS_LOWER,
    CENSUS_UNAVAILABLE,
    _resolve_uploader_guild_async,
    _sanitize_world,
    _validate_character_name,
    _validate_payload_signature,
)
from backend.server.api.raid_planning import _require_member, _require_officer
from backend.server.auth_deps import is_admin, require_user_session_or_token
from backend.server.core.audit_log import audit_log
from backend.server.core.session_user import SessionUser, TokenUser
from backend.server.db import get_display_names_for_discord_ids, has_role
from backend.server.db.attendance import store as attendance_db
from backend.server.db.availability import store as availability_db
from backend.server.db.raid_planning import store as planning_db
from backend.server.db.raid_schedule import store as schedule_db
from backend.server.limiter import limiter, upload_rate_key
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["attendance"])

_MAX_RAID = 100
_MAX_ONLINE = 1000
_CLOCK_PAST_S = 36 * 3600
_CLOCK_FUTURE_S = 3600


async def _ensure_subscriber(user: SessionUser | TokenUser) -> None:
    """Limited-preview gate: every attendance route (uploads, mains, guild
    views) needs the 'subscriber' role while the feature is dark for the
    wider userbase. Admins always pass. The parser treats the 403 like any
    other failure — uploads stay quiet, DKP falls back to the bulk grant."""
    if is_admin(cast("SessionUser", user)):  # id-only check — token shape is fine
        return
    if not await has_role(str(user["id"]), "subscriber"):
        raise HTTPException(status_code=403, detail="Attendance tracking is in limited preview.")


class AttendanceMemberIn(BaseModel):
    name: str
    first_seen: int
    last_seen: int


class AttendanceIngestRequest(BaseModel):
    logger_name: str
    logger_server: str
    guild_name: str | None = None  # informational only — server resolves + verifies
    sent_at: int
    raid_members: list[AttendanceMemberIn] = Field(default_factory=list, max_length=_MAX_RAID)
    online_guildies: list[AttendanceMemberIn] = Field(default_factory=list, max_length=_MAX_ONLINE)
    zones: list[str] = Field(default_factory=list, max_length=20)


class AttendanceIngestResponse(BaseModel):
    status: str  # created | merged
    session_id: int
    session_day: str
    raid_members: int
    online_guildies: int
    scheduled: bool


def _clean_members(raw: list[AttendanceMemberIn], now: int) -> list[dict]:
    """Validate names + clamp timestamps; garbage rows drop silently (one
    mangled /who line must never kill a snapshot)."""
    lo, hi = now - _CLOCK_PAST_S, now + _CLOCK_FUTURE_S
    out = []
    for m in raw:
        name = _validate_character_name(m.name)
        if name is None or m.first_seen > m.last_seen:
            continue
        first = min(max(m.first_seen, lo), hi)
        last = min(max(m.last_seen, lo), hi)
        out.append({"name": name.capitalize(), "first_seen": first, "last_seen": last})
    return out


@router.post("/attendance/ingest", response_model=AttendanceIngestResponse, status_code=201)
@limiter.limit("30/minute", key_func=upload_rate_key)
async def ingest_attendance(request: Request, body: AttendanceIngestRequest) -> AttendanceIngestResponse:
    user = await require_user_session_or_token(request)
    await _ensure_subscriber(user)
    await _validate_payload_signature(request, user)

    logger_name = _validate_character_name(body.logger_name)
    if logger_name is None:
        raise HTTPException(status_code=400, detail="logger_name is invalid.")
    world = _sanitize_world(body.logger_server)
    if not world:
        raise HTTPException(status_code=400, detail="logger_server is missing or malformed.")
    if world.lower() not in _ALLOWED_SERVERS_LOWER:
        raise HTTPException(status_code=403, detail=f"Server '{world}' is not tracked here.")

    # The uploader's guild is resolved server-side (cache/store first; one
    # live Census call on a true cold miss — EQ2Parser's timeout is generous).
    guild = await _resolve_uploader_guild_async(logger_name.capitalize(), world, allow_census=True)
    if guild is CENSUS_UNAVAILABLE:
        raise HTTPException(
            status_code=503, detail="Census unavailable — could not verify your guild. Try again shortly."
        )
    if not isinstance(guild, str) or not guild:
        raise HTTPException(
            status_code=403, detail=f"'{logger_name}' has no guild — attendance uploads are guild-scoped."
        )

    import time as _time

    now = int(_time.time())
    raid = _clean_members(body.raid_members, now)
    online = _clean_members(body.online_guildies, now)
    if not raid and not online:
        raise HTTPException(status_code=400, detail="Snapshot carries no valid members.")
    zones = [z.strip()[:64] for z in body.zones if z and z.strip()][:20]

    # Schedule probe: start / mid / end of the snapshot window against every
    # team. The result freezes onto the session so later edits keep history.
    points = [m["first_seen"] for m in raid + online] + [m["last_seen"] for m in raid + online]
    win = (min(points), max(points))
    teams = await schedule_db.get_schedule(world, guild)
    scheduled, team_index = False, None
    for i, team in enumerate(teams):
        for ts in (win[0], (win[0] + win[1]) // 2, win[1]):
            if raid_live.team_scheduled_at(team, ts):
                scheduled, team_index = True, i
                break
        if scheduled:
            break

    result = await attendance_db.apply_snapshot(
        world=world,
        guild_name=guild,
        discord_id=str(user["id"]),
        sent_at=body.sent_at,
        raid_members=raid,
        online_guildies=online,
        zones=zones,
        scheduled=scheduled,
        team_index=team_index,
    )
    audit_log(
        "attendance_ingest",
        actor=str(user["id"]),
        guild=guild,
        world=world,
        session_id=result["session_id"],
        raid=len(raid),
        online=len(online),
        merged=result["merged"],
    )
    return AttendanceIngestResponse(
        status="merged" if result["merged"] else "created",
        session_id=result["session_id"],
        session_day=result["session_day"],
        raid_members=len(raid),
        online_guildies=len(online),
        scheduled=scheduled,
    )


@router.get("/attendance/mains")
@limiter.limit("30/minute")
async def get_raid_mains(request: Request, character: str, server: str) -> dict:
    """The parser's DKP-substitution table: {rostered character → raid main}.

    Raiders map to themselves; raid alts map to their owner's main (the
    claimed character rostered as 'raider', primary claim preferred) so the
    parser can award DKP to the main even when the player is on an alt.
    Best effort — unclaimed alts map to themselves. Auth + guild resolution
    mirror the ingest route (bearer token; guild resolved from the logging
    character server-side)."""
    user = await require_user_session_or_token(request)
    await _ensure_subscriber(user)

    name = _validate_character_name(character)
    if name is None:
        raise HTTPException(status_code=400, detail="character is invalid.")
    world = _sanitize_world(server)
    if not world:
        raise HTTPException(status_code=400, detail="server is missing or malformed.")
    if world.lower() not in _ALLOWED_SERVERS_LOWER:
        raise HTTPException(status_code=403, detail=f"Server '{world}' is not tracked here.")

    guild = await _resolve_uploader_guild_async(name.capitalize(), world, allow_census=True)
    if guild is CENSUS_UNAVAILABLE:
        raise HTTPException(status_code=503, detail="Census unavailable — try again shortly.")
    if not isinstance(guild, str) or not guild:
        raise HTTPException(status_code=403, detail=f"'{name}' has no guild.")

    role_rows = await planning_db.get_roles(world, guild)
    claims = await planning_db.claims_map(world)
    primaries = await planning_db.primary_claims(world)
    _, char_mains = derive.resolve_mains(role_rows, claims, primaries)
    return {"world": world, "guild": guild, "mains": char_mains}


# ---------------------------------------------------------------------------
# Guild views
# ---------------------------------------------------------------------------


async def _derivation_inputs(world: str, guild_name: str, session_day: str) -> tuple[dict, dict, dict, dict]:
    role_rows = await planning_db.get_roles(world, guild_name)
    roles = {r["character_name"].lower(): r["role"] for r in role_rows}
    claims = await planning_db.claims_map(world)
    primaries = await planning_db.primary_claims(world)
    user_mains, _ = derive.resolve_mains(role_rows, claims, primaries)
    afk_by_user = await availability_db.statuses_for_day(session_day)
    return roles, claims, afk_by_user, user_mains


@router.get("/guild/{guild_name}/attendance")
@limiter.limit("30/minute")
async def list_attendance(request: Request, guild_name: str, limit: int = 25, before: int | None = None) -> dict:
    _validate_guild_name(guild_name)
    viewer, is_officer = await _require_member(request, guild_name)
    await _ensure_subscriber(viewer)
    world = current_world()

    limit = max(1, min(limit, 100))
    sessions = await attendance_db.list_sessions(world, guild_name, limit=limit, before_id=before)
    obs_by_session = await attendance_db.observations_for_sessions([s["id"] for s in sessions])

    claims = await planning_db.claims_map(world)
    roles_rows = await planning_db.get_roles(world, guild_name)
    roles = {r["character_name"].lower(): r["role"] for r in roles_rows}

    out = []
    for s in sessions:
        afk_by_user = await availability_db.statuses_for_day(s["session_day"])
        char_rows, _ = derive.derive_categories(
            obs_by_session.get(s["id"], []), roles, claims, afk_by_user, bool(s["scheduled"])
        )
        out.append(
            {
                **s,
                "zones": json.loads(s["zones"] or "[]"),
                "scheduled": bool(s["scheduled"]),
                "counts": derive.session_counts(char_rows),
            }
        )
    return {"is_officer": is_officer, "sessions": out}


@router.get("/guild/{guild_name}/attendance/{session_id}")
@limiter.limit("30/minute")
async def get_attendance_session(request: Request, guild_name: str, session_id: int) -> dict:
    _validate_guild_name(guild_name)
    viewer, is_officer = await _require_member(request, guild_name)
    await _ensure_subscriber(viewer)
    world = current_world()

    session = await attendance_db.get_session(session_id)
    if session is None or session["world"] != world or session["guild_name"].lower() != guild_name.lower():
        raise HTTPException(status_code=404, detail="Attendance session not found.")

    obs = await attendance_db.observations_for_session(session_id)
    roles, claims, afk_by_user, user_mains = await _derivation_inputs(
        world, session["guild_name"], session["session_day"]
    )
    char_rows, user_rows = derive.derive_categories(
        obs, roles, claims, afk_by_user, bool(session["scheduled"]), user_mains
    )

    display = await get_display_names_for_discord_ids([u["discord_id"] for u in user_rows])
    for u in user_rows:
        u["display_name"] = display.get(u["discord_id"]) or f"User {u['discord_id'][-4:]}"

    session["scheduled"] = bool(session["scheduled"])
    session["zones"] = json.loads(session["zones"] or "[]")
    # Uploader discord ids are an audit detail — officers only.
    session["uploaders"] = sorted(json.loads(session["uploaders"] or "{}")) if is_officer else None
    return {"is_officer": is_officer, "session": session, "characters": char_rows, "users": user_rows}


@router.delete("/guild/{guild_name}/attendance/{session_id}")
@limiter.limit("10/minute")
async def delete_attendance_session(request: Request, guild_name: str, session_id: int) -> dict:
    _validate_guild_name(guild_name)
    user = await _require_officer(request, guild_name)
    await _ensure_subscriber(user)
    world = current_world()

    session = await attendance_db.get_session(session_id)
    if session is None or session["world"] != world or session["guild_name"].lower() != guild_name.lower():
        raise HTTPException(status_code=404, detail="Attendance session not found.")
    deleted = await attendance_db.delete_session(session_id)
    if deleted:
        audit_log("attendance_session_deleted", actor=user["id"], guild=guild_name, session_id=session_id)
    return {"deleted": deleted}
