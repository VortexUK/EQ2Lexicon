"""GET /character/{name}/gear-sets — the character's saved in-game
equipment sets (Census ``adventure_sets`` collection).

Mirrors the AA read path: serve last-known data instantly from
census_store, refresh from Census only in the background, fall through
to one live fetch for never-seen characters. The character's Census id
comes from the (already cached) character record, so a warm character
page costs zero extra Census calls to render the tabs.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import HTTPException
from pydantic import BaseModel

from backend.census.store import StoreRecord
from backend.census.store import store as census_store
from backend.core.log_safety import scrub
from backend.eq2db.items import catalogue as _items
from backend.server.api.character import router
from backend.server.api.character.stat_deltas import compute_stat_deltas
from backend.server.api.character.views import (
    AdornSlotResponse,
    EquipmentSlotResponse,
    _adorn_ilvl_bonus,
    _build_char_response,
    _equipment_lookup_ids,
    _heal_equipment_placeholders,
    _ilvl_from_gear,
)
from backend.server.cache import character_cache, gear_sets_cache
from backend.server.constants import CHARACTER_STALE_S
from backend.server.core.cache_keys import char_cache_key, gear_sets_cache_key
from backend.server.core.census_lifecycle import shared_census_client
from backend.server.core.executor import run_sync
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)


class GearSetResponse(BaseModel):
    name: str
    ilvl: float | None = None  # average item level of the set's gear
    # Approximate sheet-stat movement of equipping this set instead of the
    # current gear: CharacterStats field → (set − worn). See stat_deltas.py.
    stat_deltas: dict[str, float] = {}
    equipment: list[EquipmentSlotResponse] = []


class CharGearSetsResponse(BaseModel):
    character_name: str
    sets: list[GearSetResponse] = []


def _sets_response_from_census(
    name: str, sets: list, current_equipment: list[EquipmentSlotResponse]
) -> CharGearSetsResponse:
    """Census GearSet dataclasses → response models, with per-set average
    ilvl computed through the same gear map the character sheet uses and
    stat deltas approximated against the currently worn gear."""
    out: list[GearSetResponse] = []
    for gs in sets:
        slots = [
            EquipmentSlotResponse(
                slot=s.slot_name,
                name=s.item_name,
                item_id=s.item_id,
                icon_id=s.icon_id,
                tier=s.tier,
                adorn_slots=[
                    AdornSlotResponse(color=a.color, adorn_name=a.adorn_name, adorn_id=a.adorn_id)
                    for a in s.adorn_slots
                ],
            )
            for s in gs.equipment
        ]
        gear = _items.gear_for_ids(_equipment_lookup_ids(slots))
        for slot in slots:
            for adorn in slot.adorn_slots:
                adorn.ilvl_bonus = _adorn_ilvl_bonus(adorn, gear)
        out.append(
            GearSetResponse(
                name=gs.name,
                ilvl=_ilvl_from_gear(slots, gear),
                stat_deltas=compute_stat_deltas(slots, current_equipment),
                equipment=slots,
            )
        )
    return CharGearSetsResponse(character_name=name, sets=out)


async def _character(name: str):
    """The character's response record (id + worn equipment) via the normal
    store-first character read. 404s when the character is unknown everywhere."""
    cache_key = char_cache_key(name, current_world())
    cached, _ = character_cache.get_stale(cache_key)
    if cached is not None:
        return cached
    async with shared_census_client() as client:
        char = await client.get_character(name, current_world())
    if char is None:
        raise HTTPException(status_code=404, detail=f"Character '{name}' not found on {current_world()}")
    result = _build_char_response(char)
    character_cache.set(cache_key, result)
    return result


async def _fetch_and_build(name: str) -> CharGearSetsResponse | None:
    """One live Census round-trip → response model. None on Census failure."""
    char = await _character(name)
    async with shared_census_client() as client:
        sets = await client.get_gear_sets(char.id)
    if sets is None:
        return None
    resp = _sets_response_from_census(name, sets, char.equipment)
    for gs in resp.sets:
        await _heal_equipment_placeholders(gs.equipment)
    return resp


async def _bg_refresh_gear_sets(name: str, cache_key: str) -> None:
    """Background task: silently re-fetch, persist to census_store, update
    the in-memory cache."""
    try:
        world = current_world()
        result = await _fetch_and_build(name)
        if result is None:
            return
        now = int(time.time())

        def _write() -> None:
            conn = census_store.init_db()
            try:
                census_store.upsert_character_gear_sets(conn, name, world, result.model_dump(), now=now)
            finally:
                conn.close()

        await run_sync(_write)
        gear_sets_cache.set(cache_key, result)
    except Exception as exc:
        _log.warning("[cache] Background gear-sets refresh failed for %s: %s", scrub(name), exc)


@router.get("/character/{name}/gear-sets", response_model=CharGearSetsResponse)
async def get_character_gear_sets(name: str) -> CharGearSetsResponse:
    """Serve last-known gear sets instantly; refresh in the background."""
    world = current_world()
    cache_key = gear_sets_cache_key(name, world)
    now = int(time.time())

    cached, is_stale = gear_sets_cache.get_stale(cache_key)
    if cached is not None:
        if is_stale:
            asyncio.create_task(_bg_refresh_gear_sets(name, cache_key))
        return cached

    def _read() -> StoreRecord | None:
        conn = census_store.init_db()
        try:
            return census_store.get_character_gear_sets(conn, name, world)
        finally:
            conn.close()

    rec = await run_sync(_read)
    if rec is not None:
        stale = (now - rec["last_resolved_at"]) > CHARACTER_STALE_S
        if stale:
            asyncio.create_task(_bg_refresh_gear_sets(name, cache_key))
        resp = CharGearSetsResponse(**rec["data"])
        gear_sets_cache.set(cache_key, resp)
        return resp

    # Never seen — one live fetch.
    from backend.server import census_health

    if census_health.is_down():
        _log.debug("[gear-sets] Skipping live fetch — census_health=down (name=%s)", scrub(name))
        raise HTTPException(
            status_code=503,
            detail=f"'{name}' gear sets not cached yet and Census is unavailable. Try again shortly.",
        )
    try:
        result = await _fetch_and_build(name)
    except HTTPException:
        raise
    except Exception:
        result = None
    if result is None:
        raise HTTPException(
            status_code=503,
            detail=f"'{name}' gear sets not cached yet and Census is unavailable. Try again shortly.",
        )

    def _write() -> None:
        conn = census_store.init_db()
        try:
            census_store.upsert_character_gear_sets(conn, name, world, result.model_dump(), now=now)
        finally:
            conn.close()

    await run_sync(_write)
    gear_sets_cache.set(cache_key, result)
    return result
