"""Raid-attendance routes — EQ2Parser snapshot ingest + the guild Attendance tab.

Ingest uses the parses upload contract (bearer token + HMAC over the
uncompressed JSON) and resolves the uploader's guild server-side from
logger_name — the client never asserts its own guild. Categories
(present / sat_out / afk / awol) are derived at read time by
backend/server/attendance.py.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.server import attendance as derive
from backend.server import raid_live
from backend.server.api.guild import _validate_guild_name
from backend.server.api.parses.ingest import (
    _ALLOWED_SERVERS_LOWER,
    CENSUS_UNAVAILABLE,
    REJECT_HEADER,
    _resolve_uploader_guild_async,
    _sanitize_world,
    _uploader_claimed,
    _validate_character_name,
    _validate_payload_signature,
)
from backend.server.api.raid_planning import _require_member, _require_officer
from backend.server.auth_deps import is_admin, require_user_session_or_token
from backend.server.core.audit_log import audit_log
from backend.server.core.executor import run_sync
from backend.server.core.session_user import SessionUser, TokenUser
from backend.server.core.validation import CHARACTER_NAME_MAX_LENGTH, WORLD_NAME_MAX_LENGTH
from backend.server.db import get_display_names_for_discord_ids, has_role
from backend.server.db.attendance import MAX_SESSION_SPAN_S, MERGE_GAP_S, ROLLOVER_S
from backend.server.db.attendance import store as attendance_db
from backend.server.db.availability import merge_availability
from backend.server.db.availability import store as availability_db
from backend.server.db.raid_planning import store as planning_db
from backend.server.db.raid_schedule import store as schedule_db
from backend.server.limiter import limiter, upload_rate_key
from backend.server.parses.db import store as parses_db
from backend.server.parses.pet_detection import is_known_pet_name
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["attendance"])

_MAX_RAID = 100
_MAX_ONLINE = 1000
_CLOCK_PAST_S = 36 * 3600
_CLOCK_FUTURE_S = 3600
# One raid night never spans this long. Pre-0.5.4 parsers accumulated state
# for days (no session lifecycle) and uploaded windows stretching back to
# the clock clamp — those snapshots minted day-long phantom sessions.
_MAX_WINDOW_S = 12 * 3600


async def _ensure_subscriber(user: SessionUser | TokenUser) -> None:
    """Limited-preview gate: every attendance route (uploads, mains, guild
    views) needs the 'subscriber' role while the feature is dark for the
    wider userbase. Admins always pass.

    The 403 is deliberately untagged (no ``REJECT_HEADER``): it is
    account-level. By the rule documented on ``REJECT_HEADER`` in
    api/parses/ingest.py, the header marks a refusal that another request
    from the same token could get past, and nothing from a token without the
    role gets past this gate.

    What the parser does with it: its attendance snapshot and its raid-mains
    lookup are sent outside the parse-upload queue and a failure of either
    is ignored, so this 403 does NOT pause parse uploads (the queue's pause
    on an untagged 403 applies to responses to parse uploads only). The
    parser also asks /api/auth/whoami for the role first and sends no
    snapshot without it, so it normally never sees this response. With no
    mains table its DKP falls back to the bulk raid grant."""
    if is_admin(cast("SessionUser", user)):  # id-only check — token shape is fine
        return
    if not await has_role(str(user["id"]), "subscriber"):
        # No REJECT_HEADER: see the docstring. Every attendance request from
        # this token is refused until an admin grants the role.
        raise HTTPException(status_code=403, detail="Attendance tracking is in limited preview.")


class AttendanceMemberIn(BaseModel):
    name: str
    first_seen: int
    last_seen: int


class AttendanceIngestRequest(BaseModel):
    # Bounded at the lengths the route's validators enforce (character-name
    # shape 15, server-name pattern 31): a longer value is a 422 from the
    # model. A shorter value of the wrong shape, or an empty one, is still
    # the route's 400. The member names in the two lists below are NOT
    # bounded here on purpose: _clean_members drops a bad row silently, and
    # a max_length would turn one mangled /who line into a refused snapshot.
    logger_name: str = Field(max_length=CHARACTER_NAME_MAX_LENGTH)
    logger_server: str = Field(max_length=WORLD_NAME_MAX_LENGTH)
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


async def _schedule_probe(world: str, guild: str, win: tuple[int, int]) -> tuple[bool, int | None]:
    """Was any team scheduled during the window? Probes start/mid/end so a
    raid that started late or ended early still matches its slot."""
    teams = await schedule_db.get_schedule(world, guild)
    for i, team in enumerate(teams):
        for ts in (win[0], (win[0] + win[1]) // 2, win[1]):
            if raid_live.team_scheduled_at(team, ts):
                return True, i
    return False, None


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
    # The three 403s below carry REJECT_HEADER (see its comment in
    # api/parses/ingest.py): each is about the character or server in THIS
    # snapshot, and the same token succeeds from another character.
    if world.lower() not in _ALLOWED_SERVERS_LOWER:
        # server-not-tracked: the log is from a server the site does not
        # serve. A snapshot logged on a served server would be accepted.
        raise HTTPException(
            status_code=403,
            detail=f"Server '{world}' is not tracked here.",
            headers={REJECT_HEADER: "server-not-tracked"},
        )
    # Attendance writes land in the logger's GUILD, so the logger must be a
    # character this account has an approved claim on (admins excepted).
    if not is_admin(user) and not await _uploader_claimed(str(user["id"]), logger_name, world):
        # no-claim: this logging character is not an approved claim of the
        # account on this world. A snapshot logged on a character the account
        # has claimed would be accepted, and so are this account's parse
        # uploads (parse ingest stores an unclaimed logger as unverified).
        raise HTTPException(
            status_code=403,
            detail=f"'{logger_name}' is not a character claimed by your account on {world}.",
            headers={REJECT_HEADER: "no-claim"},
        )

    # The uploader's guild is resolved server-side (cache/store first; one
    # live Census call on a true cold miss — EQ2Parser's timeout is generous).
    guild = await _resolve_uploader_guild_async(logger_name.capitalize(), world, allow_census=True)
    if guild is CENSUS_UNAVAILABLE:
        raise HTTPException(
            status_code=503, detail="Census unavailable — could not verify your guild. Try again shortly."
        )
    if not isinstance(guild, str) or not guild:
        # no-guild: this character is unguilded, so there is no guild to file
        # the snapshot under. A guilded character of the same account works.
        raise HTTPException(
            status_code=403,
            detail=f"'{logger_name}' has no guild — attendance uploads are guild-scoped.",
            headers={REJECT_HEADER: "no-guild"},
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
    if win[1] - win[0] > _MAX_WINDOW_S:
        raise HTTPException(
            status_code=422,
            detail="Snapshot spans an implausibly long window for one raid night — update EQ2Parser.",
        )
    scheduled, team_index = await _schedule_probe(world, guild, win)

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


#: Reconstruction only trusts raid-sized fights (the parses raid bucket) —
#: a 6-man dungeon earlier that evening is not the raid.
_RECONSTRUCT_MIN_PLAYERS = 7


def _raid_night(clusters: list[list[dict]], teams: list[dict]) -> list[dict]:
    """The fights that make up the raid night.

    With a raid schedule: every fight that starts inside one of the guild's
    scheduled raid windows, with the schedule's own grace (the same rule the
    live attendance check uses), and nothing else. Without a schedule, or
    when no fight falls in one: the cluster with the most fights, as before.

    2026-10-18: Paragon's day was an open-world event fought on and off from
    12:16 to 02:25 with gaps under the merge gap, so it was one cluster and
    reconstruction listed 100+ people instead of the 19:00-22:00 raid."""
    if teams:
        scheduled = [
            f for c in clusters for f in c if any(raid_live.team_scheduled_at(t, f["started_at"]) for t in teams)
        ]
        if scheduled:
            return sorted(scheduled, key=lambda f: f["started_at"])
    return max(clusters, key=len)


def _parse_roster_sync(
    world: str, guild_name: str, day: str, teams: list[dict] | None = None
) -> tuple[str | None, list[dict], list[str], int]:
    """Rebuild a raid roster for one session-day evening from the guild's
    parse uploads — the "forgot /whoraid" recovery path. Every ally player
    across the evening's raid-sized fights becomes a raid member, their
    first/last-seen spanning the fights they appear in. Fights cluster by
    the session merge gap; :func:`_raid_night` picks the raid night from
    them, by the guild's raid schedule (``teams``) when it has one. Returns
    (canonical_guild, members, zones, fight_count) — SYNC, runs in the
    executor."""
    # API-layer helpers imported locally to keep the module-load DAG
    # cycle-free (same pattern as cleanup.py / parse_posts.py).
    from backend.server.api.parses.list import _PLAYER_COUNT_SQL, _ensure_classified  # noqa: PLC0415

    d = dt.date.fromisoformat(day)
    # session_day = date(started_at - 6h UTC), so day D covers fights whose
    # started_at falls in [D 06:00 UTC, D+1 06:00 UTC).
    win_start = int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.UTC).timestamp()) + ROLLOVER_S
    # lower(e.guild_name) = lower(%s) rides idx_encounters_world_guild_lower
    # (case-insensitive guild match).
    sql = (
        f"SELECT e.id, e.guild_name, e.zone, e.started_at, e.ended_at, ({_PLAYER_COUNT_SQL}) AS player_count "
        "FROM encounters e WHERE e.world = %s AND lower(e.guild_name) = lower(%s) AND e.hidden_at IS NULL "
        "AND e.started_at >= %s AND e.started_at < %s"
    )
    conn = parses_db.init_db()
    try:
        rows = [dict(r) for r in conn.execute(sql, (world, guild_name, win_start, win_start + 86_400)).fetchall()]
        for r in rows:
            # EQ2Parser up to v0.6.1 stored year 1 as the end of a fight in
            # which no ally dealt damage; a fight never ends before it starts.
            if r["ended_at"] is None or r["ended_at"] < r["started_at"]:
                r["ended_at"] = r["started_at"]
            if _ensure_classified(conn, r["id"], r.get("zone")):
                refreshed = conn.execute(
                    "SELECT COUNT(*) AS n FROM combatants WHERE encounter_id = %s AND is_player = 1", (r["id"],)
                ).fetchone()
                r["player_count"] = int(refreshed["n"]) if refreshed else 0
        rows = [r for r in rows if (r.get("player_count") or 0) >= _RECONSTRUCT_MIN_PLAYERS]
        if not rows:
            return None, [], [], 0

        rows.sort(key=lambda r: r["started_at"])
        clusters: list[list[dict]] = [[rows[0]]]
        for r in rows[1:]:
            if r["started_at"] - clusters[-1][-1]["ended_at"] > MERGE_GAP_S:
                clusters.append([])
            clusters[-1].append(r)
        fights = _raid_night(clusters, teams or [])

        combatants = parses_db.get_combatants_for_encounters(conn, [f["id"] for f in fights])
        members: dict[str, dict] = {}
        for f in fights:
            for c in combatants.get(f["id"], []):
                if not c.get("ally") or not c.get("is_player"):
                    continue
                name = _validate_character_name(c.get("name") or "")
                # Known-pet re-check by NAME, not just the persisted flag:
                # historic combatant rows classified before a pet name joined
                # the blocklist (Bonecruncher/Mistrunner) still carry
                # is_player=1, and reconstruction reads exactly those rows.
                if name is None or is_known_pet_name(name):
                    continue
                m = members.get(name.lower())
                if m is None:
                    members[name.lower()] = {
                        "name": name.capitalize(),
                        "first_seen": f["started_at"],
                        "last_seen": f["ended_at"],
                    }
                else:
                    m["first_seen"] = min(m["first_seen"], f["started_at"])
                    m["last_seen"] = max(m["last_seen"], f["ended_at"])
        zones = sorted({f["zone"] for f in fights if f.get("zone")})[:20]
        return fights[0]["guild_name"], list(members.values()), zones, len(fights)
    finally:
        conn.close()


class ReconstructInput(BaseModel):
    date: str = Field(min_length=10, max_length=10)  # session day, YYYY-MM-DD


@router.post("/guild/{guild_name}/attendance/reconstruct")
@limiter.limit("10/minute")
async def reconstruct_attendance(request: Request, guild_name: str, body: ReconstructInput) -> dict:
    """Officer recovery for a forgotten /whoraid: rebuild the night's raid
    roster from the guild's own parse uploads and feed it through the
    normal snapshot path — merging into whatever partial session exists,
    or creating the session outright. Re-running is safe (the observation
    upsert is commutative)."""
    _validate_guild_name(guild_name)
    user = await _require_officer(request, guild_name)
    await _ensure_subscriber(user)
    world = current_world()
    try:
        dt.date.fromisoformat(body.date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc

    teams = await schedule_db.get_schedule(world, guild_name)
    guild, members, zones, fight_count = await run_sync(_parse_roster_sync, world, guild_name, body.date, teams)
    if guild is None or not members:
        raise HTTPException(
            status_code=404,
            detail="No raid-sized parses found for that evening — nothing to reconstruct from.",
        )

    # Best-effort alt→main attribution: a player who swapped characters
    # mid-night (or raided purely on an alt) credits their MAIN — one roster
    # row under the main's name with the alts' intervals merged in.
    # Resolution comes from approved claims + raid-planner roles (the same
    # table the parser's DKP substitution uses); unclaimed characters keep
    # their own row.
    role_rows = await planning_db.get_roles(world, guild)
    claims = await planning_db.claims_map(world)
    primaries = await planning_db.primary_claims(world)
    _, char_mains = derive.resolve_mains(role_rows, claims, primaries)
    mains_lower = {alt.lower(): main for alt, main in char_mains.items()}
    merged: dict[str, dict] = {}
    for m in members:
        name = str(m["name"])
        main = mains_lower.get(name.lower(), name)
        cur = merged.get(main.lower())
        if cur is None:
            merged[main.lower()] = {"name": main, "first_seen": m["first_seen"], "last_seen": m["last_seen"]}
        else:
            cur["first_seen"] = min(cur["first_seen"], m["first_seen"])
            cur["last_seen"] = max(cur["last_seen"], m["last_seen"])
    alts_merged = len(members) - len(merged)
    members = list(merged.values())[:_MAX_RAID]

    import time as _time  # noqa: PLC0415

    points = [m["first_seen"] for m in members] + [m["last_seen"] for m in members]
    win = (min(points), max(points))
    scheduled, team_index = await _schedule_probe(world, guild, win)
    result = await attendance_db.apply_snapshot(
        world=world,
        guild_name=guild,
        discord_id=str(user["id"]),
        sent_at=int(_time.time()),
        raid_members=members,
        online_guildies=[],
        zones=zones,
        scheduled=scheduled,
        team_index=team_index,
    )
    audit_log(
        "attendance_reconstructed",
        actor=str(user["id"]),
        guild=guild,
        world=world,
        session_id=result["session_id"],
        day=body.date,
        raid=len(members),
        fights=fight_count,
        alts_merged=alts_merged,
        merged=result["merged"],
    )
    return {
        "status": "merged" if result["merged"] else "created",
        "session_id": result["session_id"],
        "session_day": result["session_day"],
        "raid_members": len(members),
        "fights": fight_count,
        "scheduled": scheduled,
    }


@router.get("/attendance/mains")
@limiter.limit("30/minute")
async def get_raid_mains(
    request: Request,
    character: Annotated[str, Query(max_length=CHARACTER_NAME_MAX_LENGTH)],
    server: Annotated[str, Query(max_length=WORLD_NAME_MAX_LENGTH)],
) -> dict:
    """The parser's DKP-substitution table: {rostered character → raid main}.

    Raiders map to themselves; raid alts map to their owner's main (the
    claimed character rostered as 'raider', primary claim preferred) so the
    parser can award DKP to the main even when the player is on an alt.
    Best effort — unclaimed alts map to themselves. Auth + guild resolution
    mirror the ingest route (bearer token; guild resolved from the logging
    character server-side).

    ``character`` and ``server`` carry the same length bounds as the ingest
    body's logger_name / logger_server: over-long is a 422 before auth, the
    wrong shape is the 400 below."""
    user = await require_user_session_or_token(request)
    await _ensure_subscriber(user)

    name = _validate_character_name(character)
    if name is None:
        raise HTTPException(status_code=400, detail="character is invalid.")
    world = _sanitize_world(server)
    if not world:
        raise HTTPException(status_code=400, detail="server is missing or malformed.")
    # Same classification as the ingest route: both 403s below are about the
    # character/server asked for, not the token, so they carry REJECT_HEADER.
    # (This route has no claim check, so there is no no-claim refusal here.)
    if world.lower() not in _ALLOWED_SERVERS_LOWER:
        # server-not-tracked: asking for a character on a served server works.
        raise HTTPException(
            status_code=403,
            detail=f"Server '{world}' is not tracked here.",
            headers={REJECT_HEADER: "server-not-tracked"},
        )

    guild = await _resolve_uploader_guild_async(name.capitalize(), world, allow_census=True)
    if guild is CENSUS_UNAVAILABLE:
        raise HTTPException(status_code=503, detail="Census unavailable — try again shortly.")
    if not isinstance(guild, str) or not guild:
        # no-guild: an unguilded character has no roster to map; the same
        # token gets a table for a guilded character.
        raise HTTPException(
            status_code=403,
            detail=f"'{name}' has no guild.",
            headers={REJECT_HEADER: "no-guild"},
        )

    role_rows = await planning_db.get_roles(world, guild)
    claims = await planning_db.claims_map(world)
    primaries = await planning_db.primary_claims(world)
    _, char_mains = derive.resolve_mains(role_rows, claims, primaries)
    return {"world": world, "guild": guild, "mains": char_mains}


# ---------------------------------------------------------------------------
# Guild views
# ---------------------------------------------------------------------------


async def _merged_char_availability(world: str, day: str, claims: dict[str, str]) -> dict[str, str]:
    """Per-character availability verdict for ``day`` — the same
    newest-edit-wins merge the raid planner shows, so attendance excuses
    match what officers see there."""
    user_times = await availability_db.statuses_for_day_with_times(day)
    char_times = await availability_db.char_statuses_for_day_with_times(world, day)
    return merge_availability(char_times, user_times, claims)


async def _derivation_inputs(world: str, guild_name: str, session_day: str) -> tuple[dict, dict, dict, dict, dict]:
    role_rows = await planning_db.get_roles(world, guild_name)
    roles = {r["character_name"].lower(): r["role"] for r in role_rows}
    claims = await planning_db.claims_map(world)
    primaries = await planning_db.primary_claims(world)
    user_mains, _ = derive.resolve_mains(role_rows, claims, primaries)
    afk_by_user = await availability_db.statuses_for_day(session_day)
    avail_by_char = await _merged_char_availability(world, session_day, claims)
    return roles, claims, afk_by_user, avail_by_char, user_mains


@router.get("/guild/{guild_name}/attendance")
@limiter.limit("30/minute")
async def list_attendance(request: Request, guild_name: str, limit: int = 25, before: int | None = None) -> dict:
    _validate_guild_name(guild_name)
    viewer, is_officer = await _require_member(request, guild_name)
    await _ensure_subscriber(viewer)
    world = current_world()

    limit = max(1, min(limit, 100))
    sessions = await attendance_db.list_sessions(world, guild_name, limit=limit, before_id=before)
    session_ids = [s["id"] for s in sessions]
    obs_by_session = await attendance_db.observations_for_sessions(session_ids)
    overrides_by_session = await attendance_db.overrides_for_sessions(session_ids)
    segments_by_session = await attendance_db.segments_for_sessions(session_ids)

    claims = await planning_db.claims_map(world)
    roles_rows = await planning_db.get_roles(world, guild_name)
    roles = {r["character_name"].lower(): r["role"] for r in roles_rows}

    out = []
    for s in sessions:
        afk_by_user = await availability_db.statuses_for_day(s["session_day"])
        afk_by_char = await _merged_char_availability(world, s["session_day"], claims)
        char_rows, _ = derive.derive_categories(
            obs_by_session.get(s["id"], []),
            roles,
            claims,
            afk_by_user,
            bool(s["scheduled"]),
            overrides=overrides_by_session.get(s["id"], {}),
            afk_by_char=afk_by_char,
            segments_by_char=segments_by_session.get(s["id"], {}),
            window=(s["started_at"], s["ended_at"]),
        )
        out.append(
            {
                **s,
                "zones": s["zones"] or [],  # jsonb — psycopg hands back the parsed list
                "scheduled": bool(s["scheduled"]),
                "counts": derive.session_counts(char_rows),
            }
        )
    return {"is_officer": is_officer, "sessions": out}


@router.get("/guild/{guild_name}/attendance/summary")
@limiter.limit("30/minute")
async def attendance_summary(request: Request, guild_name: str, limit: int = 25) -> dict:
    """The matrix view: one row per player, one cell per session (newest
    first), with per-row attendance % (present + sat_out over the window).
    Cells carry first/last-seen + the characters observed so the frontend
    can tooltip clock-in/out."""
    _validate_guild_name(guild_name)
    viewer, is_officer = await _require_member(request, guild_name)
    await _ensure_subscriber(viewer)
    world = current_world()

    limit = max(1, min(limit, 50))
    sessions = await attendance_db.list_sessions(world, guild_name, limit=limit)
    session_ids = [s["id"] for s in sessions]
    obs_by_session = await attendance_db.observations_for_sessions(session_ids)
    overrides_by_session = await attendance_db.overrides_for_sessions(session_ids)
    segments_by_session = await attendance_db.segments_for_sessions(session_ids)

    role_rows = await planning_db.get_roles(world, guild_name)
    roles = {r["character_name"].lower(): r["role"] for r in role_rows}
    claims = await planning_db.claims_map(world)
    primaries = await planning_db.primary_claims(world)
    user_mains, _ = derive.resolve_mains(role_rows, claims, primaries)

    per_session = []
    for s in sessions:
        afk_by_user = await availability_db.statuses_for_day(s["session_day"])
        afk_by_char = await _merged_char_availability(world, s["session_day"], claims)
        char_rows, user_rows = derive.derive_categories(
            obs_by_session.get(s["id"], []),
            roles,
            claims,
            afk_by_user,
            bool(s["scheduled"]),
            user_mains,
            overrides=overrides_by_session.get(s["id"], {}),
            afk_by_char=afk_by_char,
            segments_by_char=segments_by_session.get(s["id"], {}),
            window=(s["started_at"], s["ended_at"]),
        )
        per_session.append((s["id"], char_rows, user_rows))

    rows = derive.summarize_attendance(per_session)
    # Pure-alt players have no raid main — fall back to their site name.
    display = await get_display_names_for_discord_ids([r["discord_id"] for r in rows if r["discord_id"]])
    for r in rows:
        if r["name"] is None and r["discord_id"] is not None:
            r["name"] = display.get(r["discord_id"]) or f"User {r['discord_id'][-4:]}"

    return {
        "is_officer": is_officer,
        "sessions": [
            {
                "id": s["id"],
                "session_day": s["session_day"],
                "seq": s["seq"],
                "started_at": s["started_at"],
                "ended_at": s["ended_at"],
                "scheduled": bool(s["scheduled"]),
            }
            for s in sessions
        ],
        "rows": rows,
    }


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
    overrides = await attendance_db.overrides_for_session(session_id)
    segments = await attendance_db.segments_for_session(session_id)
    roles, claims, afk_by_user, afk_by_char, user_mains = await _derivation_inputs(
        world, session["guild_name"], session["session_day"]
    )
    char_rows, user_rows = derive.derive_categories(
        obs,
        roles,
        claims,
        afk_by_user,
        bool(session["scheduled"]),
        user_mains,
        overrides=overrides,
        afk_by_char=afk_by_char,
        segments_by_char=segments,
        window=(session["started_at"], session["ended_at"]),
    )

    corrector_ids = sorted(
        {ov["set_by"] for ov in overrides.values()} | {segs[0]["set_by"] for segs in segments.values() if segs}
    )
    display = await get_display_names_for_discord_ids([u["discord_id"] for u in user_rows] + corrector_ids)
    for u in user_rows:
        u["display_name"] = display.get(u["discord_id"]) or f"User {u['discord_id'][-4:]}"
    for row in char_rows:
        if row["overridden"]:
            ov = overrides[row["name"].lower()]
            row["override_by"] = display.get(ov["set_by"]) or ov["set_by"]
        if row["manual_timeline"]:
            segs = segments.get(row["name"].lower())
            if segs:
                row["timeline_by"] = display.get(segs[0]["set_by"]) or segs[0]["set_by"]

    session["scheduled"] = bool(session["scheduled"])
    session["zones"] = session["zones"] or []  # jsonb — already parsed
    # Uploader discord ids are an audit detail — officers only.
    session["uploaders"] = sorted(session["uploaders"] or {}) if is_officer else None
    return {"is_officer": is_officer, "session": session, "characters": char_rows, "users": user_rows}


class OverrideInput(BaseModel):
    character_name: str = Field(min_length=1, max_length=32)
    category: str | None = None  # present/sat_out/afk/awol/absent; null clears


async def _officer_session(request: Request, guild_name: str, session_id: int) -> tuple[SessionUser, dict]:
    """Shared gate for the correction endpoints: officer + subscriber +
    the session actually belongs to this guild on this world."""
    _validate_guild_name(guild_name)
    user = await _require_officer(request, guild_name)
    await _ensure_subscriber(user)
    session = await attendance_db.get_session(session_id)
    if session is None or session["world"] != current_world() or session["guild_name"].lower() != guild_name.lower():
        raise HTTPException(status_code=404, detail="Attendance session not found.")
    return user, session


@router.put("/guild/{guild_name}/attendance/{session_id}/override")
@limiter.limit("60/minute")
async def put_attendance_override(request: Request, guild_name: str, session_id: int, body: OverrideInput) -> dict:
    """Officer correction: pin a category onto one character for this
    session (category null clears the correction and the derived truth
    returns). Works for never-observed characters too — that's how a
    missed raider is added by hand."""
    user, _ = await _officer_session(request, guild_name, session_id)
    name = _validate_character_name(body.character_name)
    if name is None:
        raise HTTPException(status_code=400, detail="character_name is invalid.")
    name = name.capitalize()

    if body.category is None:
        cleared = await attendance_db.clear_override(session_id, name)
        if cleared:
            audit_log(
                "attendance_override_cleared",
                actor=str(user["id"]),
                guild=guild_name,
                session_id=session_id,
                character=name,
            )
        return {"ok": True, "cleared": cleared}

    if body.category not in derive.CATEGORY_ORDER:
        raise HTTPException(status_code=400, detail=f"category must be one of {derive.CATEGORY_ORDER}.")
    await attendance_db.set_override(session_id, name, body.category, set_by=str(user["id"]))
    audit_log(
        "attendance_override_set",
        actor=str(user["id"]),
        guild=guild_name,
        session_id=session_id,
        character=name,
        category=body.category,
    )
    return {"ok": True, "cleared": False}


class WindowInput(BaseModel):
    started_at: int
    ended_at: int


@router.put("/guild/{guild_name}/attendance/{session_id}/window")
@limiter.limit("30/minute")
async def put_attendance_window(request: Request, guild_name: str, session_id: int, body: WindowInput) -> dict:
    """Officer fix for a runaway session window (a chain of overnight
    online-only merges once walked one from 19:02 to 10:57): set the
    session's own start/end. Derived timelines clamp to the window, so this
    repairs every polluted row at once. session_day/seq stay frozen."""
    user, _ = await _officer_session(request, guild_name, session_id)
    if body.started_at >= body.ended_at:
        raise HTTPException(status_code=400, detail="The session must start before it ends.")
    if body.ended_at - body.started_at > MAX_SESSION_SPAN_S:
        raise HTTPException(status_code=400, detail="Window exceeds the maximum session span.")
    await attendance_db.set_session_window(session_id, body.started_at, body.ended_at)
    audit_log(
        "attendance_window_set",
        actor=str(user["id"]),
        guild=guild_name,
        session_id=session_id,
        started_at=body.started_at,
        ended_at=body.ended_at,
    )
    return {"ok": True}


class SegmentIn(BaseModel):
    category: str
    started_at: int
    ended_at: int


class SegmentsInput(BaseModel):
    character_name: str = Field(min_length=1, max_length=32)
    segments: list[SegmentIn] = Field(default_factory=list, max_length=24)  # [] clears


@router.put("/guild/{guild_name}/attendance/{session_id}/segments")
@limiter.limit("60/minute")
async def put_attendance_segments(request: Request, guild_name: str, session_id: int, body: SegmentsInput) -> dict:
    """Officer timeline edit: replace one character's timed periods for this
    session (present/sat_out/afk with start/end — 'sat out for an hour' is a
    period, not a whole-night verdict). An empty list reverts to the
    parser-derived timeline. Works for never-observed characters too — the
    'add a missed player with times' case."""
    user, session = await _officer_session(request, guild_name, session_id)
    name = _validate_character_name(body.character_name)
    if name is None:
        raise HTTPException(status_code=400, detail="character_name is invalid.")
    name = name.capitalize()

    segs = sorted(
        ({"category": s.category, "started_at": s.started_at, "ended_at": s.ended_at} for s in body.segments),
        key=lambda s: (s["started_at"], s["ended_at"]),
    )
    lo, hi = session["started_at"] - MERGE_GAP_S, session["ended_at"] + MERGE_GAP_S
    prev_end: int | None = None
    for s in segs:
        if s["category"] not in derive.SEGMENT_CATEGORIES:
            raise HTTPException(
                status_code=400, detail=f"segment category must be one of {list(derive.SEGMENT_CATEGORIES)}."
            )
        if s["started_at"] >= s["ended_at"]:
            raise HTTPException(status_code=400, detail="A period must start before it ends.")
        if s["started_at"] < lo or s["ended_at"] > hi:
            raise HTTPException(status_code=400, detail="Period times fall too far outside the session window.")
        if prev_end is not None and s["started_at"] < prev_end:
            raise HTTPException(status_code=400, detail="Periods overlap — they must be sequential.")
        prev_end = s["ended_at"]

    await attendance_db.set_segments(session_id, name, segs, set_by=str(user["id"]))
    audit_log(
        "attendance_timeline_set",
        actor=str(user["id"]),
        guild=guild_name,
        session_id=session_id,
        character=name,
        segments=len(segs),
    )
    return {"ok": True, "cleared": not segs}


@router.delete("/guild/{guild_name}/attendance/{session_id}/character/{character_name}")
@limiter.limit("60/minute")
async def delete_attendance_character(request: Request, guild_name: str, session_id: int, character_name: str) -> dict:
    """Officer row removal — for junk names (mis-parses): the character's
    raid/online observations and any override are deleted together."""
    user, _ = await _officer_session(request, guild_name, session_id)
    name = _validate_character_name(character_name)
    if name is None:
        raise HTTPException(status_code=400, detail="character_name is invalid.")
    removed = await attendance_db.remove_character(session_id, name)
    if removed:
        audit_log(
            "attendance_character_removed",
            actor=str(user["id"]),
            guild=guild_name,
            session_id=session_id,
            character=name,
        )
    return {"removed": removed}


class BulkDeleteInput(BaseModel):
    session_ids: list[int] = Field(min_length=1, max_length=200)


@router.post("/guild/{guild_name}/attendance/bulk-delete")
@limiter.limit("10/minute")
async def bulk_delete_attendance_sessions(request: Request, guild_name: str, body: BulkDeleteInput) -> dict:
    """Officer moderation at cleanup scale — a broken parser once minted
    dozens of phantom sessions a day, and deleting them one endpoint call
    at a time was untenable. Sessions that don't belong to this guild on
    this world are silently skipped, so a stale id can't fail the batch."""
    _validate_guild_name(guild_name)
    user = await _require_officer(request, guild_name)
    await _ensure_subscriber(user)
    world = current_world()

    deleted: list[int] = []
    for session_id in dict.fromkeys(body.session_ids):
        session = await attendance_db.get_session(session_id)
        if session is None or session["world"] != world or session["guild_name"].lower() != guild_name.lower():
            continue
        if await attendance_db.delete_session(session_id):
            deleted.append(session_id)
    if deleted:
        audit_log(
            "attendance_sessions_bulk_deleted",
            actor=str(user["id"]),
            guild=guild_name,
            count=len(deleted),
            session_ids=",".join(map(str, deleted)),
        )
    return {"deleted": len(deleted)}


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
