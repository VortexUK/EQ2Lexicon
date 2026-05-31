from __future__ import annotations

import asyncio
import logging
import time

import aiosqlite
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.census import store as census_store
from backend.core.log_safety import scrub as _scrub
from backend.server.cache import guild_cache
from backend.server.core.cache_keys import guild_adorns_key, guild_info_key, guild_roster_key, guild_spells_key
from backend.server.core.census_lifecycle import shared_census_client
from backend.server.core.validation import validate_guild_name as _validate_guild_name_lib
from backend.server.db import DB_PATH as _USERS_DB_PATH
from backend.server.db import get_active_claims
from backend.server.guild_cache import (
    _bg_refresh_guild,
    _fetch_and_cache_guild,
    _persist_and_publish_guild,
)
from backend.server.limiter import limiter
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

# Re-export for backward compat: callers that imported _overview_to_char_response
# from this module (none currently, but keeps the surface stable during Phase 2c).
from backend.server.guild_cache import _overview_to_char_response  # noqa: E402,F401

router = APIRouter(tags=["guild"])


_OFFICER_RANKS = frozenset({0, 1})  # rank_ids that count as "officer"


def _validate_guild_name(guild_name: str) -> None:
    """Raise 400 if guild_name looks malformed or dangerously long.

    Wraps web.lib.validation.validate_guild_name so call sites are unchanged."""
    if _validate_guild_name_lib(guild_name) is None:
        raise HTTPException(
            status_code=400,
            detail="Guild name is invalid (letters, digits, spaces, hyphens, apostrophes; max 64 chars).",
        )


# ---------------------------------------------------------------------------
# Models — roster
# ---------------------------------------------------------------------------


class GuildInfoResponse(BaseModel):
    name: str
    world: str
    dateformed: int | None = None
    description: str | None = None
    alignment: int | str | None = None  # Census returns an int (0/1/2) or None
    type: int | str | None = None  # Census may return an int here too
    level: int | None = None
    members: int | None = None
    accounts: int | None = None
    achievement_count: int = 0
    fetched_at: int | None = None
    stale: bool = False


class GuildMemberResponse(BaseModel):
    name: str
    level: int | None = None
    cls: str | None = None
    ts_class: str | None = None
    ts_level: int | None = None
    aa_level: int | None = None
    ilvl: float | None = None  # average gear item level
    deity: str | None = None
    rank: str | None = None
    rank_id: int | None = None
    guild_status: int | None = None  # status points contributed to the guild
    played_time: int | None = None  # total /played seconds


class GuildResponse(BaseModel):
    name: str
    world: str
    members: list[GuildMemberResponse]
    fetched_at: int | None = None
    stale: bool = False


# ---------------------------------------------------------------------------
# Models — spell check
# ---------------------------------------------------------------------------


class MemberSpellTiers(BaseModel):
    name: str
    rank: str | None = None
    rank_id: int | None = None
    tiers: dict[str, int]  # tier_name → count  (all _TIER_ORDER keys present)
    total: int
    spell_names: dict[str, list[str]] = {}  # tier_name → spell names, sorted by level desc


class GuildSpellCheckResponse(BaseModel):
    guild_name: str
    world: str
    tiers: list[str]  # ordered list of tier columns that have any data
    members: list[MemberSpellTiers]


# ---------------------------------------------------------------------------
# Models — adorn check
# ---------------------------------------------------------------------------


class AdornColorStats(BaseModel):
    filled: int
    total: int


class MemberAdornStats(BaseModel):
    name: str
    rank: str | None = None
    rank_id: int | None = None
    adorns: dict[str, AdornColorStats]  # colour → stats
    missing: dict[str, list[str]] = {}  # colour → slot names with empty adorn of that colour


class GuildAdornCheckResponse(BaseModel):
    guild_name: str
    world: str
    colors: list[str]  # ordered colour columns that appear in the data
    members: list[MemberAdornStats]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _int(v: object) -> int | None:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Route-level guild auth helpers (stay in guild.py — depend on current_world)
# ---------------------------------------------------------------------------


async def _roster_rank_map(guild_name: str) -> dict[str, int | None]:
    """
    Return {member_name_lower: rank_id} for a guild.
    Serves from the cached roster when available; on miss triggers a full
    guild fetch via _fetch_and_cache_guild (which also pre-warms every other
    cache key so the roster endpoint and character pages are all warm too).
    """
    roster, _ = guild_cache.get_stale(guild_roster_key(guild_name, current_world()))
    if roster is not None:
        return {m.name.lower(): m.rank_id for m in roster.members}
    full = await _fetch_and_cache_guild(guild_name)
    if not full:
        return {}
    guild_data, _, _ = full
    return {m.name.lower(): m.rank_id for m in guild_data.members}


async def _officer_chars(discord_id: str, guild_name: str) -> set[str]:
    """
    Return the set of this user's approved character names (lower-cased) that
    hold an officer rank (rank_id in _OFFICER_RANKS) in the named guild.
    Empty set means the user is not an officer of this guild.
    """
    claims_data = await get_active_claims(discord_id, world=current_world())
    approved = {c["character_name"].lower() for c in claims_data["approved"]}
    if not approved:
        return set()
    rank_map = await _roster_rank_map(guild_name)
    return {name for name in approved if rank_map.get(name) in _OFFICER_RANKS}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/guild/{guild_name}/info", response_model=GuildInfoResponse)
@limiter.limit("10/minute")
async def get_guild_info(request: Request, guild_name: str) -> GuildInfoResponse:
    """
    Return lightweight guild metadata (no member list).
    The info cache is pre-warmed by any guild endpoint that calls
    _fetch_and_cache_guild, so this usually hits cache on first load.
    On a fresh cache hit returns immediately.  On a stale hit or full miss:
    checks the durable census_store (using the roster blob to derive member
    count), and only calls Census when the store is also empty and Census is up.
    """
    from backend.server import census_health
    from backend.server.census_refresh import request_guild_refresh

    _validate_guild_name(guild_name)
    info_key = guild_info_key(guild_name, current_world())
    cached, is_stale = guild_cache.get_stale(info_key)
    if cached is not None and not is_stale:
        return cached
    # Fall through to the durable store (the stored blob is the roster shape;
    # derive a minimal GuildInfoResponse from it — name/world + member count).
    conn = census_store.init_db(census_store.DB_PATH)
    try:
        rec = census_store.get_guild(conn, guild_name, current_world())
    finally:
        conn.close()
    if rec is not None:
        age = int(time.time()) - rec["last_resolved_at"]
        stale = age > 900
        if stale:
            request_guild_refresh(guild_name)
        info_data = rec["data"].get("info")
        if info_data:
            info_resp = GuildInfoResponse(**{**info_data, "fetched_at": rec["last_resolved_at"], "stale": stale})
        else:
            # older/partial row without info — degrade gracefully to what the roster gives us
            roster_data = rec["data"]["roster"]
            info_resp = GuildInfoResponse(
                name=roster_data.get("name", guild_name),
                world=roster_data.get("world", current_world()),
                members=len(roster_data.get("members", [])),
                fetched_at=rec["last_resolved_at"],
                stale=stale,
            )
        guild_cache.set(info_key, info_resp)
        return info_resp
    # Never seen in the store.
    if census_health.is_down():
        raise HTTPException(
            status_code=503,
            detail=f"Guild '{guild_name}' not cached yet and Census is unavailable.",
        )
    try:
        await _persist_and_publish_guild(guild_name)
    except Exception as exc:
        _log.warning("[guild] Live fetch failed for %s: %s", _scrub(guild_name), exc)
        raise HTTPException(
            status_code=503,
            detail=f"Census error while fetching guild '{guild_name}'.",
        ) from exc
    result, _ = guild_cache.get_stale(info_key)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found on {current_world()}.")
    return result


@router.get("/guild/{guild_name}", response_model=GuildResponse)
@limiter.limit("10/minute")
async def get_guild(request: Request, guild_name: str) -> GuildResponse:
    """
    Return the guild roster sorted by rank then level descending.
    On a fresh cache hit returns immediately.  On a stale hit or full miss:
    checks the durable census_store first (serving stored data if Census is
    down), and only calls Census when both the store is empty and Census is up.
    """
    from backend.server import census_health
    from backend.server.census_refresh import request_guild_refresh

    _validate_guild_name(guild_name)
    cache_key = guild_roster_key(guild_name, current_world())
    cached, is_stale = guild_cache.get_stale(cache_key)
    if cached is not None and not is_stale:
        return cached
    conn = census_store.init_db(census_store.DB_PATH)
    try:
        rec = census_store.get_guild(conn, guild_name, current_world())
    finally:
        conn.close()
    if rec is not None:
        age = int(time.time()) - rec["last_resolved_at"]
        stale = age > 900
        if stale:
            request_guild_refresh(guild_name)
        stored = rec["data"]
        roster_data = stored["roster"]
        resp = GuildResponse(**{**roster_data, "fetched_at": rec["last_resolved_at"], "stale": stale})
        guild_cache.set(cache_key, resp)
        return resp
    # Never seen in the store — need a live Census fetch.
    if census_health.is_down():
        raise HTTPException(
            status_code=503,
            detail=f"Guild '{guild_name}' not cached yet and Census is unavailable.",
        )
    try:
        await _persist_and_publish_guild(guild_name)
    except Exception as exc:
        _log.warning("[guild] Live fetch failed for %s: %s", _scrub(guild_name), exc)
        raise HTTPException(
            status_code=503,
            detail=f"Census error while fetching guild '{guild_name}'.",
        ) from exc
    final_cached, _ = guild_cache.get_stale(cache_key)
    if final_cached is None:
        raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found on {current_world()}.")
    return final_cached


@router.get("/guild/{guild_name}/spell-check", response_model=GuildSpellCheckResponse)
@limiter.limit("10/minute")
async def guild_spell_check(request: Request, guild_name: str) -> GuildSpellCheckResponse:
    """
    Spell tier summary for every guild member.
    Responds instantly from cache; on miss triggers a full guild fetch
    (one Census call) that also warms roster/chars/adorns.
    Spell IDs are resolved locally — no per-character Census calls needed.
    """
    _validate_guild_name(guild_name)
    cache_key = guild_spells_key(guild_name, current_world())
    cached, is_stale = guild_cache.get_stale(cache_key)
    if cached is not None:
        if is_stale:
            # Spawned within the request context: asyncio.create_task copies the
            # contextvar, so current_world() inside the task resolves to THIS
            # request's server even after the middleware resets it post-response.
            asyncio.create_task(_bg_refresh_guild(guild_name))
        return cached
    full = await _fetch_and_cache_guild(guild_name)
    if full is None:
        raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found on {current_world()}.")
    result, _ = guild_cache.get_stale(cache_key)
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="Spells database unavailable — run scripts/download_spells.py first.",
        )
    return result


@router.get("/guild/{guild_name}/adorn-check", response_model=GuildAdornCheckResponse)
@limiter.limit("10/minute")
async def guild_adorn_check(request: Request, guild_name: str) -> GuildAdornCheckResponse:
    """
    Adornment slot summary for every guild member.
    Responds instantly from cache; on miss triggers a full guild fetch
    (one Census call) that also warms roster/chars/spells.
    """
    _validate_guild_name(guild_name)
    cache_key = guild_adorns_key(guild_name, current_world())
    cached, is_stale = guild_cache.get_stale(cache_key)
    if cached is not None:
        if is_stale:
            # Spawned within the request context: asyncio.create_task copies the
            # contextvar, so current_world() inside the task resolves to THIS
            # request's server even after the middleware resets it post-response.
            asyncio.create_task(_bg_refresh_guild(guild_name))
        return cached
    full = await _fetch_and_cache_guild(guild_name)
    if full is None:
        raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found on {current_world()}.")
    result, _ = guild_cache.get_stale(cache_key)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No adorn data found for '{guild_name}'.")
    return result


# ---------------------------------------------------------------------------
# Guild name search (local DB)
# ---------------------------------------------------------------------------


class GuildNameResult(BaseModel):
    name: str


class GuildSearchResponse(BaseModel):
    results: list[GuildNameResult]
    total: int


@router.get("/guilds/search", response_model=GuildSearchResponse)
async def search_guilds(name: str = "") -> GuildSearchResponse:
    """
    Search guilds by name prefix on the configured world.
    Queries Census first; falls back to locally-tracked guilds (item-watch)
    if Census is unavailable.
    Requires at least 2 characters.
    """
    q = name.strip()
    if len(q) < 2:
        return GuildSearchResponse(results=[], total=0)
    if len(q) > 64:
        return GuildSearchResponse(results=[], total=0)

    try:
        async with shared_census_client() as client:
            raw = await client.search_guilds_by_name(q, current_world())
    except Exception as exc:
        _log.warning("[guild] Census guild search failed for %r: %s", q, exc)
        raw = []

    if raw:
        results = [GuildNameResult(name=r["name"]) for r in raw]
        return GuildSearchResponse(results=results, total=len(results))

    # Census failed — fall back to locally-tracked guilds in item_watch
    async with aiosqlite.connect(_USERS_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT DISTINCT guild_name
            FROM item_watch
            WHERE LOWER(guild_name) LIKE ?
            ORDER BY guild_name
            LIMIT 25
            """,
            (f"{q.lower()}%",),
        ) as cur:
            rows = await cur.fetchall()

    results = [GuildNameResult(name=r["guild_name"]) for r in rows]
    return GuildSearchResponse(results=results, total=len(results))
