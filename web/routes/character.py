from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from census.client import CensusClient

router = APIRouter(tags=["character"])

_WORLD = os.getenv("EQ2_WORLD", "Varsoon")
_SERVICE_ID = os.getenv("CENSUS_SERVICE_ID", "example")


class EquipmentSlotResponse(BaseModel):
    slot: str
    name: str
    item_id: str | None = None
    icon_id: str | None = None
    tier: str | None = None


class CharacterResponse(BaseModel):
    id: str
    name: str
    level: int | None = None
    cls: str | None = None
    race: str | None = None
    gender: str | None = None
    deity: str | None = None
    aa_count: int = 0
    world: str
    ts_class: str | None = None
    ts_level: int | None = None
    equipment: list[EquipmentSlotResponse] = []


@router.get("/character/{name}", response_model=CharacterResponse)
async def get_character(name: str) -> CharacterResponse:
    """Fetch a character's overview from the EQ2 Census API."""
    client = CensusClient(service_id=_SERVICE_ID)
    try:
        char = await client.get_character(name, _WORLD)
    finally:
        await client.close()

    if char is None:
        raise HTTPException(status_code=404, detail=f"Character '{name}' not found on {_WORLD}")

    return CharacterResponse(
        id        = char.id,
        name      = char.name,
        level     = char.level,
        cls       = char.cls,
        race      = char.race,
        gender    = char.gender,
        deity     = char.deity,
        aa_count  = char.aa_count,
        world     = char.world,
        ts_class  = char.ts_class,
        ts_level  = char.ts_level,
        equipment = [
            EquipmentSlotResponse(
                slot    = s.slot_name,
                name    = s.item_name,
                item_id = s.item_id,
                icon_id = s.icon_id,
                tier    = s.tier,
            )
            for s in char.equipment
        ],
    )
