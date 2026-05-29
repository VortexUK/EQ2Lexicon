from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from census.client import CensusClient
from web.cache import character_cache, guild_cache
from web.config import SERVICE_ID as _SERVICE_ID
from web.db import (
    add_item_watch,
    get_active_claims,
    list_item_watches,
    remove_item_watch,
    update_item_watch_check,
)
from web.routes.guild import _officer_chars, _roster_rank_map, _validate_guild_name
from web.server_context import current_world

router = APIRouter(tags=["guild"])


# ---------------------------------------------------------------------------
# Models — item watch
# ---------------------------------------------------------------------------


class ItemWatchEntry(BaseModel):
    id: int
    character_name: str
    item_id: int
    item_name: str
    added_by_name: str
    added_at: int
    first_seen_at: int | None = None
    last_seen_at: int | None = None
    last_checked_at: int | None = None


class AddItemWatchRequest(BaseModel):
    character_name: str
    item_name: str  # resolved server-side to item_id + canonical display name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _check_watch(watch: dict) -> None:
    """
    Check whether the watched character currently has the item equipped,
    using the character_cache.  Updates the DB regardless of result.

    Uses the watch row's own ``world`` field (not ``current_world()``) so that
    the background sweep — which iterates watches from a single world — still
    resolves the correct cache key even if called outside a request context.
    """
    row_world = watch.get("world", current_world())
    name_key = f"{watch['character_name'].lower()}:{row_world.lower()}"
    cached, _ = character_cache.get_stale(name_key)
    if cached is None:
        return  # no data available yet — skip, will check later
    item_id_str = str(watch["item_id"])
    seen = any(s.item_id == item_id_str for s in cached.equipment)
    await update_item_watch_check(watch["id"], seen)


async def _check_all_watches(guild_name: str, world: str) -> None:
    """Background task: check every watch entry for a guild/server against the cache."""
    watches = await list_item_watches(guild_name, world=world)
    for w in watches:
        try:
            await _check_watch(w)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Item watch endpoints
# ---------------------------------------------------------------------------


@router.get("/guild/{guild_name}/item-watch", response_model=list[ItemWatchEntry])
async def get_item_watches(guild_name: str, request: Request) -> list[ItemWatchEntry]:
    """
    List all item watch entries for this guild.
    Triggers a background equipment check for all entries so statuses
    are updated against the latest cached character data.
    Officer access required.
    """
    _validate_guild_name(guild_name)
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not await _officer_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")

    world = current_world()
    watches = await list_item_watches(guild_name, world=world)
    # Fire background check to freshen statuses; return current DB state immediately
    asyncio.create_task(_check_all_watches(guild_name, world=world))
    return [ItemWatchEntry(**w) for w in watches]


@router.post("/guild/{guild_name}/item-watch", response_model=ItemWatchEntry, status_code=201)
async def add_item_watch_entry(
    guild_name: str,
    body: AddItemWatchRequest,
    request: Request,
) -> ItemWatchEntry:
    """
    Add a new item watch.  The item_name is resolved against the local items DB
    (falling back to the Census API) to get a canonical display name and ID.
    Returns 409 if the same item is already being watched for that character.
    Officer access required.
    """
    _validate_guild_name(guild_name)
    from census import db as item_db

    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not await _officer_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")

    # Validate character is in this guild
    rank_map = await _roster_rank_map(guild_name)
    char_key = body.character_name.strip().lower()
    if char_key not in rank_map:
        raise HTTPException(
            status_code=404,
            detail=f"'{body.character_name}' is not a member of {guild_name}.",
        )

    # Resolve item — local DB first, then Census
    item_name = body.item_name.strip()
    raw = await item_db.find_by_name(item_name)
    if raw is None:
        # CENSUS-CLIENT-LIFECYCLE: migrate to web.lib.census_lifecycle.shared_census_client (Phase 2c.2)
        client = CensusClient(service_id=_SERVICE_ID)
        try:
            raw = await client.get_raw_item(item_name)
            if raw:
                item_list = raw.get("item_list") or []
                raw = item_list[0] if item_list else None
        finally:
            await client.close()
    if raw is None:
        raise HTTPException(
            status_code=404,
            detail=f"Item '{item_name}' not found. Check the spelling.",
        )

    item_id = int(raw["id"])
    item_name = raw.get("displayname") or item_name

    # Canonical character name from roster (correct capitalisation)
    canon_name = next(
        (n for n in rank_map if n == char_key),
        body.character_name.strip(),
    )
    # Try to get properly capitalised name from the cached roster response
    roster_cache_key = f"roster:{guild_name.lower()}:{current_world().lower()}"
    roster, _ = guild_cache.get_stale(roster_cache_key)
    if roster:
        match = next((m.name for m in roster.members if m.name.lower() == char_key), None)
        if match:
            canon_name = match

    # Use the officer's primary in-game character name as the attribution,
    # falling back to their Discord display name if no primary is set.
    officer_claims = await get_active_claims(user["id"], world=current_world())
    primary_claim = next((c for c in officer_claims["approved"] if c.get("is_primary")), None)
    added_by_name = (
        primary_claim["character_name"]
        if primary_claim
        else (user.get("global_name") or user.get("username", "Unknown"))
    )

    try:
        row = await add_item_watch(
            guild_name=guild_name,
            character_name=canon_name,
            item_id=item_id,
            item_name=item_name,
            added_by=user["id"],
            added_by_name=added_by_name,
            world=current_world(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Immediately check if the character is already wearing it
    asyncio.create_task(_check_watch(row))

    return ItemWatchEntry(**row)


@router.delete("/guild/{guild_name}/item-watch/{watch_id}", status_code=200)
async def delete_item_watch(guild_name: str, watch_id: int, request: Request) -> dict:
    """Remove an item watch entry.  Officer access required."""
    _validate_guild_name(guild_name)
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not await _officer_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")
    if not await remove_item_watch(watch_id, guild_name, world=current_world()):
        raise HTTPException(status_code=404, detail="Watch entry not found")
    return {"ok": True}
