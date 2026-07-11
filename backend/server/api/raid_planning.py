"""Raid-planning API: officer-curated raid rosters + per-team group layouts
plus the per-user availability calendar.

Visibility model:
  * GET planner data — guild members only (the viewer holds an approved
    claim on a character in this guild's roster). Officers get the same
    payload with ``is_officer: true`` so the UI enables saving.
  * Writes (roles + placements) — officers only (same rank gate as the
    item watch / raid schedule).
  * ``/me/availability`` — any authenticated user reads/writes their own
    calendar; the panel is only *shown* when ``is_raider`` is true.

The planner is date-aware but the layout is date-less: one persistent
grid per team, with availability for the requested date overlaid on it
(AFK players grey out rather than being removed).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.core.log_safety import scrub
from backend.server.api.guild import _fetch_and_cache_guild, _officer_chars, _roster_rank_map
from backend.server.auth_deps import is_admin
from backend.server.cache import guild_cache
from backend.server.core.audit_log import audit_log
from backend.server.core.cache_keys import guild_roster_key
from backend.server.core.session_user import SessionUser
from backend.server.db import get_active_claims, get_display_names_for_discord_ids
from backend.server.db.availability import store as availability_db
from backend.server.db.raid_planning import VALID_ROLES
from backend.server.db.raid_planning import store as planning_db
from backend.server.db.raid_schedule import store as raid_schedule_db
from backend.server.limiter import limiter
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["raid-planning"])

GROUPS = 4
SLOTS_PER_GROUP = 6
#: How far ahead the availability calendar reaches (~3 months).
AVAILABILITY_HORIZON_DAYS = 92


# ── Models ───────────────────────────────────────────────────────────────────


class RosterEntry(BaseModel):
    name: str
    cls: str | None = None
    level: int | None = None
    role: str | None = None  # raider | raid_alt | None
    rank: str | None = None  # guild rank name (Manage Raiders filter)
    rank_id: int | None = None


class PlacementModel(BaseModel):
    character_name: str
    group_num: int | None = None  # 1..4
    slot: int | None = None  # 0..5
    sitout: bool = False


class PlannerResponse(BaseModel):
    is_officer: bool
    team_index: int
    team_count: int
    date: str
    roster: list[RosterEntry]  # full guild roster with role overlay
    placements: list[PlacementModel]
    availability: dict[str, str]  # char_name_lower -> tentative|afk (for roled chars)
    players: dict[str, str]  # char_name_lower -> player display name (roled chars w/ claims)


class RoleInput(BaseModel):
    character_name: str = Field(min_length=1, max_length=64)
    role: str | None = None  # raider | raid_alt | None (clear)


class PlacementsInput(BaseModel):
    placements: list[PlacementModel] = Field(max_length=200)


class AvailabilityResponse(BaseModel):
    is_raider: bool
    horizon_days: int
    days: dict[str, str]  # YYYY-MM-DD -> tentative|afk (available days absent)


class AvailabilityInput(BaseModel):
    days: dict[str, str] = Field(max_length=200)  # YYYY-MM-DD -> available|tentative|afk


# ── Gates ────────────────────────────────────────────────────────────────────


def _require_session(request: Request) -> SessionUser:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return cast("SessionUser", user)


async def _member_chars(discord_id: str, guild_name: str) -> set[str]:
    """The viewer's approved character names (lower) that are in this guild."""
    claims_data = await get_active_claims(discord_id, world=current_world())
    approved = {c["character_name"].lower() for c in claims_data["approved"]}
    if not approved:
        return set()
    rank_map = await _roster_rank_map(guild_name)
    return {n for n in approved if n in rank_map}


async def _can_edit(user: SessionUser, guild_name: str) -> bool:
    """Officers edit; a site admin who is ALSO a member of this guild edits
    too (admin alone is not enough — the planner belongs to the guild)."""
    if await _officer_chars(user["id"], guild_name):
        return True
    return is_admin(user) and bool(await _member_chars(user["id"], guild_name))


async def _require_member(request: Request, guild_name: str) -> tuple[SessionUser, bool]:
    """401 without a session; 403 unless the viewer has an approved claim on
    a character in this guild. Returns (session_user, can_edit)."""
    user = _require_session(request)
    members = await _member_chars(user["id"], guild_name)
    if not members:
        raise HTTPException(status_code=403, detail="Raid planning is visible to guild members only")
    editor = bool(await _officer_chars(user["id"], guild_name)) or is_admin(user)
    return user, editor


async def _require_officer(request: Request, guild_name: str) -> SessionUser:
    user = _require_session(request)
    if not await _can_edit(user, guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")
    return user


# ── Helpers ──────────────────────────────────────────────────────────────────


def _parse_date(value: str | None) -> str:
    """Validate ?date= (default today). Returns the ISO string."""
    if not value:
        return dt.date.today().isoformat()
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc


async def _guild_roster(guild_name: str) -> list:
    """The guild's member list (cached roster, fetch-on-miss)."""
    roster, _ = guild_cache.get_stale(guild_roster_key(guild_name, current_world()))
    if roster is not None:
        return roster.members
    full = await _fetch_and_cache_guild(guild_name)
    if not full:
        raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found")
    guild_data, _, _ = full
    return guild_data.members


# ── Planner ──────────────────────────────────────────────────────────────────


@router.get("/guild/{guild_name}/raid-planning/{team_index}", response_model=PlannerResponse)
@limiter.limit("30/minute")
async def get_planner(
    request: Request,
    guild_name: str,
    team_index: int,
    date: str | None = None,
) -> PlannerResponse:
    """Everything the planner UI needs for one team, in one call."""
    user, is_officer = await _require_member(request, guild_name)
    world = current_world()
    day = _parse_date(date)

    teams = await raid_schedule_db.get_schedule(world, guild_name)
    team_count = len(teams)
    if team_index < 0 or (team_count and team_index >= team_count):
        raise HTTPException(status_code=404, detail="No such raid team")

    members = await _guild_roster(guild_name)
    roles = {r["character_name"].lower(): r["role"] for r in await planning_db.get_roles(world, guild_name)}
    roster = [
        RosterEntry(
            name=m.name,
            cls=m.cls,
            level=m.level,
            role=roles.get(m.name.lower()),
            rank=getattr(m, "rank", None),
            rank_id=getattr(m, "rank_id", None),
        )
        for m in members
    ]

    placements = [PlacementModel(**p) for p in await planning_db.get_placements(world, guild_name, team_index)]

    # Availability + player overlay for roled characters only (small set).
    roled_lower = set(roles.keys())
    claims = await planning_db.claims_map(world)
    char_to_user = {n: claims[n] for n in roled_lower if n in claims}
    statuses = await availability_db.statuses_for_day(day)
    availability = {n: statuses[uid] for n, uid in char_to_user.items() if uid in statuses}
    display = await get_display_names_for_discord_ids(sorted(set(char_to_user.values())))
    players = {n: display.get(uid, uid) for n, uid in char_to_user.items()}

    return PlannerResponse(
        is_officer=is_officer,
        team_index=team_index,
        team_count=team_count,
        date=day,
        roster=roster,
        placements=placements,
        availability=availability,
        players=players,
    )


@router.put("/guild/{guild_name}/raid-planning/roles")
@limiter.limit("60/minute")
async def put_role(request: Request, guild_name: str, body: RoleInput) -> dict:
    """Officer: designate a guild character as raider / raid alt (or clear)."""
    user = await _require_officer(request, guild_name)
    world = current_world()

    if body.role is not None and body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="role must be raider, raid_alt or null")

    # Only real guild members can be rostered — resolve canonical casing
    # from the roster so stored names match Census.
    rank_map = await _roster_rank_map(guild_name)
    if body.character_name.lower() not in rank_map:
        raise HTTPException(status_code=404, detail=f"'{body.character_name}' is not a member of {guild_name}")
    canonical = next(m.name for m in await _guild_roster(guild_name) if m.name.lower() == body.character_name.lower())

    await planning_db.set_role(world, guild_name, canonical, body.role, updated_by=user["id"])
    audit_log(
        "raid_role_set",
        actor=user["id"],
        guild=guild_name,
        character=canonical,
        role=body.role or "cleared",
    )
    return {"ok": True, "character_name": canonical, "role": body.role}


@router.put("/guild/{guild_name}/raid-planning/{team_index}/placements")
@limiter.limit("60/minute")
async def put_placements(
    request: Request,
    guild_name: str,
    team_index: int,
    body: PlacementsInput,
) -> dict:
    """Officer: replace one team's whole layout (the drag-drop save)."""
    user = await _require_officer(request, guild_name)
    world = current_world()

    teams = await raid_schedule_db.get_schedule(world, guild_name)
    if team_index < 0 or (teams and team_index >= len(teams)):
        raise HTTPException(status_code=404, detail="No such raid team")

    roles = {r["character_name"].lower(): r["role"] for r in await planning_db.get_roles(world, guild_name)}

    seen_chars: set[str] = set()
    seen_slots: set[tuple[int, int]] = set()
    clean: list[dict] = []
    for p in body.placements:
        low = p.character_name.lower()
        if low not in roles:
            raise HTTPException(status_code=400, detail=f"'{scrub(p.character_name)}' is not on the raid roster")
        if low in seen_chars:
            raise HTTPException(status_code=400, detail=f"'{scrub(p.character_name)}' appears twice")
        seen_chars.add(low)

        in_group = p.group_num is not None or p.slot is not None
        if p.sitout and in_group:
            raise HTTPException(status_code=400, detail="A character can't be in a group and on sitout")
        if in_group:
            if p.group_num is None or p.slot is None:
                raise HTTPException(status_code=400, detail="group_num and slot must be set together")
            if not (1 <= p.group_num <= GROUPS) or not (0 <= p.slot < SLOTS_PER_GROUP):
                raise HTTPException(status_code=400, detail="group_num must be 1-4 and slot 0-5")
            key = (p.group_num, p.slot)
            if key in seen_slots:
                raise HTTPException(status_code=400, detail=f"Two characters in group {p.group_num} slot {p.slot + 1}")
            seen_slots.add(key)
        clean.append(
            {
                "character_name": p.character_name,
                "group_num": p.group_num if in_group else None,
                "slot": p.slot if in_group else None,
                "sitout": p.sitout,
            }
        )

    await planning_db.replace_placements(world, guild_name, team_index, clean, updated_by=user["id"])
    audit_log(
        "raid_placements_saved",
        actor=user["id"],
        guild=guild_name,
        team_index=team_index,
        placed=len(seen_slots),
        total=len(clean),
    )
    return {"ok": True, "count": len(clean)}


# ── Personal availability ────────────────────────────────────────────────────


def _availability_window() -> tuple[dt.date, dt.date]:
    today = dt.date.today()
    return today, today + dt.timedelta(days=AVAILABILITY_HORIZON_DAYS)


@router.get("/me/availability", response_model=AvailabilityResponse)
@limiter.limit("30/minute")
async def get_my_availability(request: Request) -> AvailabilityResponse:
    """The viewer's calendar + whether any of their characters is rostered
    (drives whether the home-page panel shows at all)."""
    user = _require_session(request)
    start, end = _availability_window()

    claims_data = await get_active_claims(user["id"], world=current_world())
    my_chars = {c["character_name"].lower() for c in claims_data["approved"]}
    roled = await planning_db.roles_for_world(current_world())
    is_raider = bool(my_chars & set(roled.keys()))

    days = await availability_db.get_range(user["id"], start.isoformat(), end.isoformat())
    return AvailabilityResponse(is_raider=is_raider, horizon_days=AVAILABILITY_HORIZON_DAYS, days=days)


@router.put("/me/availability")
@limiter.limit("30/minute")
async def put_my_availability(request: Request, body: AvailabilityInput) -> dict:
    """Bulk-set the viewer's calendar days within the 3-month window."""
    user = _require_session(request)
    start, end = _availability_window()

    validated: dict[str, str] = {}
    for day_str, status in body.days.items():
        if status not in ("available", "tentative", "afk"):
            raise HTTPException(status_code=400, detail="status must be available, tentative or afk")
        try:
            day = dt.date.fromisoformat(day_str)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"bad date {scrub(day_str)!r}") from exc
        if not (start <= day <= end):
            raise HTTPException(
                status_code=400,
                detail=f"dates must be within today..+{AVAILABILITY_HORIZON_DAYS} days",
            )
        validated[day.isoformat()] = status

    await availability_db.set_days(user["id"], validated)
    return {"ok": True, "count": len(validated)}
