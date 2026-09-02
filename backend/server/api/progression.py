"""RoK progression endpoints — character sheet + guild matrix.

GET /character/{name}/progression  — one character's epic / tier / Trakanon
GET /guild/{name}/progression      — the same, rolled up per guild member

Data path: two census fetches per character (character_misc quest lists +
achievements projection), reduced immediately by backend/server/progression.py
— raw census lists are never cached or shipped. Both endpoints serve from
progression_cache stale-while-revalidate (progression changes on raid
nights, not minute-to-minute); the guild rollup batches census calls
~20 characters at a time via the comma-joined id filter.

Census-known members only (recently-logged-in players) — the standard
census caveat that applies site-wide.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.census.store import store as census_store
from backend.core.log_safety import scrub as _scrub
from backend.server.cache import progression_cache
from backend.server.core.census_lifecycle import shared_census_client
from backend.server.core.executor import run_sync
from backend.server.core.validation import validate_character_name
from backend.server.limiter import limiter
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["progression"])

#: Members below this adventure level are skipped in the guild rollup —
#: RoK raid progression is irrelevant for lowbies and every skipped member
#: saves census payload.
MIN_LEVEL = 65

#: Census multi-id batch size (comma-joined id filter).
BATCH = 20

_inflight: set[str] = set()


class ProgressionResponse(BaseModel):
    character: str
    world: str
    cls: str | None = None
    progression: dict


class GuildProgressionRow(BaseModel):
    name: str
    level: int | None = None
    cls: str | None = None
    progression: dict


class GuildProgressionResponse(BaseModel):
    guild: str
    world: str
    members: list[GuildProgressionRow]
    #: True while a cold-cache build is running server-side (poll again).
    building: bool = False


def _char_key(name: str, world: str) -> str:
    return f"prog:char:{name.lower()}:{world.lower()}"


def _guild_key(name: str, world: str) -> str:
    return f"prog:guild:{name.lower()}:{world.lower()}"


def _store_char_record(name: str, world: str) -> dict | None:
    conn = census_store.init_db()
    try:
        rec = census_store.get_character(conn, name, world)
    finally:
        conn.close()
    return rec["data"] if rec else None


async def _fetch_progressions(members: list[dict]) -> list[dict] | None:
    """Fetch + reduce progression for a list of {id, name, level, cls}
    members. Returns rows in input order, or None when census failed."""
    from backend.server.progression import reduce_progression  # noqa: PLC0415 — keep import cost off startup

    quest_by_id: dict[int, dict] = {}
    ach_by_id: dict[int, list] = {}
    async with shared_census_client() as client:
        for i in range(0, len(members), BATCH):
            ids = [m["id"] for m in members[i : i + BATCH]]
            quests = await client.get_characters_quest_data(ids)
            if quests is None:  # one retry — census drops stale keep-alives
                await asyncio.sleep(0.5)
                quests = await client.get_characters_quest_data(ids)
            achievements = await client.get_characters_achievements(ids)
            if achievements is None:
                await asyncio.sleep(0.5)
                achievements = await client.get_characters_achievements(ids)
            if quests is None or achievements is None:
                return None
            for row in quests:
                quest_by_id[row["id"]] = row
            for row in achievements:
                ach_by_id[row["id"]] = (row.get("achievements") or {}).get("achievement_list", [])

    out = []
    for m in members:
        # Store-sourced ids are strings, census reply ids are ints —
        # normalise or the store-first character path looks up nothing.
        misc = quest_by_id.get(int(m["id"]), {})
        out.append(
            {
                "name": m.get("name"),
                "level": m.get("level"),
                "cls": m.get("cls"),
                "progression": reduce_progression(
                    m.get("cls"),
                    misc.get("completed_quest_list", []) or [],
                    misc.get("quest_list", []) or [],
                    ach_by_id.get(int(m["id"]), []) or [],
                ),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Character
# ---------------------------------------------------------------------------


async def _build_character(name: str, world: str) -> ProgressionResponse | None:
    data = await run_sync(_store_char_record, name, world)
    char_id = (data or {}).get("id")
    cls = (data or {}).get("cls")
    if not char_id:
        # Never-seen character: one census lookup for id + class.
        async with shared_census_client() as client:
            brief = await client.get_character_brief(name, world)
        if not brief or not brief.get("id"):
            return None
        char_id = brief["id"]
        cls = brief.get("cls") or cls
    rows = await _fetch_progressions([{"id": char_id, "name": name, "level": None, "cls": cls}])
    if not rows:
        # The character EXISTS (we have an id) — census just failed. Callers
        # must surface 503 "try again", never 404.
        raise HTTPException(
            status_code=503, detail="Census unavailable — progression could not be fetched. Try again shortly."
        )
    result = ProgressionResponse(character=name, world=world, cls=cls, progression=rows[0]["progression"])
    progression_cache.set(_char_key(name, world), result)
    return result


async def _bg_refresh(key: str, coro_factory) -> None:
    if key in _inflight:
        return
    _inflight.add(key)
    try:
        await coro_factory()
    except Exception as exc:
        _log.warning("[progression] background refresh failed for %s: %s", _scrub(key), exc)
    finally:
        _inflight.discard(key)


@router.get("/character/{name}/progression", response_model=ProgressionResponse)
@limiter.limit("30/minute")
async def get_character_progression(request: Request, name: str) -> ProgressionResponse:
    sanitised = validate_character_name(name)
    if sanitised is None:
        raise HTTPException(status_code=400, detail="Character name is invalid.")
    canonical = sanitised.capitalize()
    world = current_world()

    key = _char_key(canonical, world)
    cached, is_stale = progression_cache.get_stale(key)
    if cached is not None:
        if is_stale:
            asyncio.create_task(_bg_refresh(key, lambda: _build_character(canonical, world)))
        return cached

    result = await _build_character(canonical, world)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Character '{canonical}' not found on {world}.")
    return result


# ---------------------------------------------------------------------------
# Guild
# ---------------------------------------------------------------------------


async def _build_guild(guild_name: str, world: str) -> GuildProgressionResponse | None:
    async with shared_census_client() as client:
        guild_id = await client.get_guild_id(guild_name, world)
        if guild_id is None:
            return None
        roster = await client.get_guild_roster_brief(guild_id)
    if roster is None:
        return None
    members = [m for m in roster if m.get("id") and m.get("name") and (m.get("level") or 0) >= MIN_LEVEL]
    members.sort(key=lambda m: (m.get("name") or "").lower())
    rows = await _fetch_progressions(members)
    if rows is None:
        # Roster resolved (guild exists) but the member fetch failed — 503.
        raise HTTPException(
            status_code=503, detail="Census unavailable — progression could not be fetched. Try again shortly."
        )
    result = GuildProgressionResponse(
        guild=guild_name,
        world=world,
        members=[GuildProgressionRow(**r) for r in rows],
    )
    progression_cache.set(_guild_key(guild_name, world), result)
    return result


@router.get("/guild/{guild_name}/progression", response_model=GuildProgressionResponse)
@limiter.limit("10/minute")
async def get_guild_progression(request: Request, guild_name: str) -> GuildProgressionResponse:
    if not guild_name or len(guild_name) > 64:
        raise HTTPException(status_code=400, detail="Guild name is invalid.")
    world = current_world()

    key = _guild_key(guild_name, world)
    cached, is_stale = progression_cache.get_stale(key)
    if cached is not None:
        if is_stale:
            asyncio.create_task(_bg_refresh(key, lambda: _build_guild(guild_name, world)))
        return cached

    result = await _build_guild(guild_name, world)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found on {world}.")
    return result
