"""Guild raid-schedule API — public read, officer-only write.

Mirrors item_watch's officer-auth pattern (`_officer_chars`) but the GET is
public. The whole schedule is replaced by one PUT (bounded: ≤4 teams, ≤4 raids/
team, each ≤5 h). Twitch links are validated + blocklist-screened; a blocklist
hit is rejected and reported to admins via audit_log.
"""

from __future__ import annotations

import logging
from zoneinfo import available_timezones

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.server import raid_live
from backend.server.api.guild import _officer_chars, _validate_guild_name
from backend.server.core.audit_log import audit_log
from backend.server.core.text_moderation import contains_blocked_term, sanitize_text
from backend.server.core.twitch import is_blocked, parse_twitch_login
from backend.server.db.raid_schedule import get_schedule, replace_schedule
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["guild"])

_MAX_TEAMS = 4
_MAX_RAIDS = 4
_MAX_SPAN_MIN = 300  # 5 hours
_MAX_TEXT_LEN = 40  # team name + raid label
_VALID_TZS = available_timezones()  # computed once at import


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class RaidSlotResponse(BaseModel):
    days: list[int]  # ISO weekdays 1=Mon..7=Sun
    start_min: int  # minutes since midnight in the team's tz
    end_min: int
    label: str | None = None


class RaidTeamResponse(BaseModel):
    name: str
    primary_tz: str
    twitch_url: str | None = None
    raids: list[RaidSlotResponse]


class RaidScheduleResponse(BaseModel):
    teams: list[RaidTeamResponse]


class RaidingLiveEntry(BaseModel):
    guild_name: str
    team_name: str
    twitch_login: str
    twitch_url: str
    viewer_count: int | None = None
    title: str | None = None
    started_at: str | None = None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class RaidSlotInput(BaseModel):
    days: list[int]
    start: str  # "HH:MM"
    end: str  # "HH:MM"
    label: str | None = None


class RaidTeamInput(BaseModel):
    name: str = ""
    primary_tz: str
    twitch_url: str | None = None
    raids: list[RaidSlotInput] = []


class RaidScheduleInput(BaseModel):
    teams: list[RaidTeamInput] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_hhmm(s: str) -> int:
    try:
        hh, mm = s.strip().split(":")
        h, m = int(hh), int(mm)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid time {s!r} — use HH:MM.") from exc
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise HTTPException(status_code=400, detail=f"Invalid time {s!r} — use HH:MM (00:00–23:59).")
    return h * 60 + m


def _slot_to_db(slot: RaidSlotInput) -> dict:
    days = sorted({d for d in slot.days if 1 <= d <= 7})
    if not days:
        raise HTTPException(status_code=400, detail="Each raid needs at least one weekday.")
    start = _parse_hhmm(slot.start)
    end = _parse_hhmm(slot.end)
    span = (end - start) % 1440  # handles a window crossing midnight
    if span == 0:
        raise HTTPException(status_code=400, detail="A raid's end time must differ from its start.")
    if span > _MAX_SPAN_MIN:
        raise HTTPException(status_code=400, detail="A raid can be at most 5 hours long.")
    label = sanitize_text(slot.label, max_len=_MAX_TEXT_LEN) or None
    return {"days": ",".join(str(d) for d in days), "start_min": start, "end_min": end, "label": label}


def _screen_text(value: str, *, actor: str, guild: str, field: str) -> None:
    """Reject + report officer free text that hits the blocklist. No-op if clean."""
    hit = contains_blocked_term(value)
    if hit:
        audit_log("suspicious_raid_text", actor=actor, guild=guild, field=field, value=value, reason=hit)
        raise HTTPException(
            status_code=400,
            detail="That text contains disallowed content and has been reported.",
        )


def _fmt_schedule(teams: list[dict]) -> RaidScheduleResponse:
    out: list[RaidTeamResponse] = []
    for t in teams:
        login = t.get("twitch_login")
        raids = [
            RaidSlotResponse(
                days=[int(d) for d in str(r["days"]).split(",") if d],
                start_min=r["start_min"],
                end_min=r["end_min"],
                label=r.get("label"),
            )
            for r in t.get("raids", [])
        ]
        out.append(
            RaidTeamResponse(
                name=t["name"],
                primary_tz=t["primary_tz"],
                twitch_url=f"https://twitch.tv/{login}" if login else None,
                raids=raids,
            )
        )
    return RaidScheduleResponse(teams=out)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/guild/{guild_name}/raid-schedule", response_model=RaidScheduleResponse)
async def get_raid_schedule(guild_name: str) -> RaidScheduleResponse:
    """Public — anyone may view a guild's raid schedule."""
    _validate_guild_name(guild_name)
    teams = await get_schedule(current_world(), guild_name)
    return _fmt_schedule(teams)


@router.get("/raiding-live", response_model=list[RaidingLiveEntry])
async def get_raiding_live() -> list[dict]:
    """Public — teams currently within a raid window AND live on Twitch, for the
    active server. Served from the poller cache (raid_live)."""
    return raid_live.get_live(current_world())


@router.put("/guild/{guild_name}/raid-schedule", response_model=RaidScheduleResponse)
async def put_raid_schedule(guild_name: str, body: RaidScheduleInput, request: Request) -> RaidScheduleResponse:
    """Officer-only — replace the guild's whole raid schedule."""
    _validate_guild_name(guild_name)
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not await _officer_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")

    if len(body.teams) > _MAX_TEAMS:
        raise HTTPException(status_code=400, detail=f"At most {_MAX_TEAMS} raid teams.")

    db_teams: list[dict] = []
    for i, team in enumerate(body.teams):
        if team.primary_tz not in _VALID_TZS:
            raise HTTPException(status_code=400, detail=f"Unknown timezone {team.primary_tz!r}.")
        if len(team.raids) > _MAX_RAIDS:
            raise HTTPException(status_code=400, detail=f"At most {_MAX_RAIDS} raids per team.")
        twitch_login = None
        if team.twitch_url and team.twitch_url.strip():
            twitch_login = parse_twitch_login(team.twitch_url)
            if twitch_login is None:
                raise HTTPException(status_code=400, detail="Stream link must be a twitch.tv channel URL.")
            hit = is_blocked(twitch_login)
            if hit:
                audit_log(
                    "suspicious_twitch_url",
                    actor=user["id"],
                    guild=guild_name,
                    url=team.twitch_url,
                    reason=hit,
                )
                raise HTTPException(
                    status_code=400,
                    detail="That stream link contains disallowed content and has been reported.",
                )
        name = sanitize_text(team.name, max_len=_MAX_TEXT_LEN)
        if name:
            _screen_text(name, actor=user["id"], guild=guild_name, field="team_name")
        raids = [_slot_to_db(s) for s in team.raids]
        for r in raids:
            if r["label"]:
                _screen_text(r["label"], actor=user["id"], guild=guild_name, field="raid_label")
        db_teams.append(
            {
                "name": name or f"Team {i + 1}",
                "primary_tz": team.primary_tz,
                "twitch_login": twitch_login,
                "raids": raids,
            }
        )

    world = current_world()
    await replace_schedule(world, guild_name, db_teams, updated_by=user["id"])
    audit_log("raid_schedule_updated", actor=user["id"], guild=guild_name, teams=len(db_teams))
    return _fmt_schedule(await get_schedule(world, guild_name))
