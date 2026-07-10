"""Character favourites — per-user bookmarks with a public count.

A favourite is a bookmark of a (character, world) pair. It is NOT ownership and
carries no guild or claim implications. Reads/writes are scoped to the active
server's world (``current_world()``).

Caching: the favourited-by-N count is cached (``favorite_count_cache``) because
character pages are hot — a cache hit skips the count query and its aiosqlite
connection entirely (an anonymous GET on a warm key does zero DB work). Writes
invalidate the key exactly (single-process asyncio), so the TTL is only a
backstop — it also self-heals the one write path that bypasses this module
(a user deletion's ON DELETE CASCADE). ``favorited_by_me`` is never cached (a
per-user point lookup on the UNIQUE index). ``GET /favorites`` is DB-direct —
once per home-page load, enriched from the in-memory character cache and the
local census store, never the network.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.census import store as census_store
from backend.server.auth_deps import require_user_session
from backend.server.cache import character_cache, favorite_count_cache
from backend.server.core.cache_keys import char_cache_key
from backend.server.core.executor import run_sync
from backend.server.core.validation import validate_character_name
from backend.server.db import favorites as favorites_db
from backend.server.db import get_active_claims
from backend.server.limiter import limiter
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["favorites"])

MAX_FAVORITES_PER_WORLD = 50


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FavoriteStatusResponse(BaseModel):
    count: int
    favorited_by_me: bool


class FavoriteEntry(BaseModel):
    character_name: str
    world: str
    created_at: int
    level: int | None = None
    cls: str | None = None
    ts_class: str | None = None
    ts_level: int | None = None
    guild_name: str | None = None


class FavoritesResponse(BaseModel):
    favorites: list[FavoriteEntry]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _canonical_name(name: str) -> str:
    """Validate + capitalise a character name from the URL (400 on invalid)."""
    sanitised = validate_character_name(name)
    if sanitised is None:
        raise HTTPException(status_code=400, detail="Character name is invalid (must be 1-15 letters).")
    return sanitised.capitalize()


def _count_key(name: str, world: str) -> str:
    return f"favcount:{name.lower()}:{world.lower()}"


async def _status(name: str, world: str, discord_id: str | None) -> FavoriteStatusResponse:
    """Count (cache-aside — a hit skips the query + connection entirely) +
    per-user membership (always fresh, only when a session exists)."""
    key = _count_key(name, world)
    count = favorite_count_cache.get(key)
    if count is None:
        count = await favorites_db.count_favorites_for_character(name, world)
        favorite_count_cache.set(key, count)
    mine = discord_id is not None and await favorites_db.is_favorited(discord_id, name, world)
    return FavoriteStatusResponse(count=count, favorited_by_me=mine)


def _store_character_data(name: str, world: str) -> dict | None:
    """Sync last-known character blob from the census store (None if never seen)."""
    return _store_character_data_many([name], world).get(name)


def _store_character_data_many(names: list[str], world: str) -> dict[str, dict]:
    """Sync batch lookup — one store connection for all names. Missing
    characters are absent from the result."""
    out: dict[str, dict] = {}
    if not names:
        return out
    conn = census_store.init_db(census_store.DB_PATH)
    try:
        for name in names:
            rec = census_store.get_character(conn, name, world)
            if rec is not None:
                out[name] = rec["data"]
    finally:
        conn.close()
    return out


async def _character_exists(name: str, world: str) -> bool:
    """Has this character ever been seen? Hot cache first, then the durable
    store — never the network (the favourite button lives on a character page
    whose load already populated the store)."""
    cached, _ = character_cache.get_stale(char_cache_key(name, world))
    if cached is not None:
        return True
    return await run_sync(_store_character_data, name, world) is not None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/character/{name}/favorite", response_model=FavoriteStatusResponse)
@limiter.limit("60/minute")
async def get_favorite_status(request: Request, name: str) -> FavoriteStatusResponse:
    """Public count + whether the current session's user favourited it."""
    canonical = _canonical_name(name)
    user = request.session.get("user")
    return await _status(canonical, current_world(), user["id"] if user else None)


async def _is_own_character(discord_id: str, name: str, world: str) -> bool:
    """Does the user hold an APPROVED claim on this character? Favouriting your
    own character is pointless and would inflate the public count."""
    claims = await get_active_claims(discord_id, world)
    return any((c.get("character_name") or "").lower() == name.lower() for c in claims["approved"])


@router.put("/character/{name}/favorite", response_model=FavoriteStatusResponse)
@limiter.limit("20/minute")
async def add_favorite(request: Request, name: str) -> FavoriteStatusResponse:
    """Favourite a character. Idempotent (PUT, not toggle — safe under
    double-click / optimistic retry). Own characters can't be favourited;
    claim approval also removes any pre-existing own-favourite (see
    db/claims.review_claim)."""
    canonical = _canonical_name(name)
    user = require_user_session(request)
    world = current_world()

    if not await _character_exists(canonical, world):
        raise HTTPException(status_code=404, detail=f"Character '{canonical}' not found on {world}.")
    if await _is_own_character(user["id"], canonical, world):
        raise HTTPException(status_code=400, detail="You can't favourite your own character.")

    if not await favorites_db.is_favorited(user["id"], canonical, world):
        # Cap enforced atomically inside the INSERT — concurrent PUTs can't
        # race a check-then-insert past it. rowcount 0 with the row still
        # absent ⇒ the cap (not an idempotent replay) blocked the write.
        added = await favorites_db.add_favorite(user["id"], canonical, world, cap=MAX_FAVORITES_PER_WORLD)
        if added:
            favorite_count_cache.delete(_count_key(canonical, world))
            _log.info("[favorites] %s favorited %s on %s", user["id"], canonical, world)
        elif not await favorites_db.is_favorited(user["id"], canonical, world):
            raise HTTPException(
                status_code=409,
                detail=f"Favourite limit reached ({MAX_FAVORITES_PER_WORLD} per server).",
            )
    return await _status(canonical, world, user["id"])


@router.delete("/character/{name}/favorite", response_model=FavoriteStatusResponse)
@limiter.limit("20/minute")
async def remove_favorite(request: Request, name: str) -> FavoriteStatusResponse:
    """Unfavourite a character. No-op safe."""
    canonical = _canonical_name(name)
    user = require_user_session(request)
    world = current_world()
    if await favorites_db.remove_favorite(user["id"], canonical, world):
        favorite_count_cache.delete(_count_key(canonical, world))
        _log.info("[favorites] %s unfavorited %s on %s", user["id"], canonical, world)
    return await _status(canonical, world, user["id"])


@router.get("/favorites", response_model=FavoritesResponse)
@limiter.limit("30/minute")
async def list_favorites(request: Request) -> FavoritesResponse:
    """The current user's favourites on the active server, enriched with
    last-known character data. A favourite whose character record is missing
    from the store renders name-only (nulls)."""
    user = require_user_session(request)
    world = current_world()
    rows = await favorites_db.list_favorites(user["id"], world)

    # Enrich: hot in-memory cache first, then ONE batched census-store pass for
    # the rest. Never touches the network.
    from_cache: dict[str, dict] = {}
    store_misses: list[str] = []
    for row in rows:
        name = row["character_name"]
        cached, _ = character_cache.get_stale(char_cache_key(name, world))
        if cached is not None:
            from_cache[name] = {
                "level": getattr(cached, "level", None),
                "cls": getattr(cached, "cls", None),
                "ts_class": getattr(cached, "ts_class", None),
                "ts_level": getattr(cached, "ts_level", None),
                "guild_name": getattr(cached, "guild_name", None),
            }
        else:
            store_misses.append(name)
    from_store = await run_sync(_store_character_data_many, store_misses, world) if store_misses else {}

    out: list[FavoriteEntry] = []
    for row in rows:
        name = row["character_name"]
        entry = FavoriteEntry(character_name=name, world=world, created_at=row["created_at"])
        data = from_cache.get(name) or from_store.get(name)
        if data:
            entry.level = data.get("level")
            entry.cls = data.get("cls")
            entry.ts_class = data.get("ts_class")
            entry.ts_level = data.get("ts_level")
            entry.guild_name = data.get("guild_name")
        out.append(entry)
    return FavoritesResponse(favorites=out)
