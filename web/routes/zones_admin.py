"""Write endpoints for the per-zone raid boss roster — add/edit/delete/reorder
encounters and add/edit/promote/delete mobs within an encounter. All gated by
require_editor (admin OR contributor). Reads still live in web/routes/zones.py;
this sibling file keeps the read/write split clean."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from census import zones_db
from web.auth_deps import require_editor
from web.lib.executor import run_sync
from web.routes.rankings import invalidate_zones_cache

router = APIRouter(tags=["zones-admin"])

# Allow-list for the type-tag mutation endpoints. v1 only permits the
# 'dungeon' tag — extending to other tokens (e.g. 'raid_x2', 'event') is a
# future cleanup. The narrow list stops a contributor from mistagging a
# zone as 'raid_x4' (which auto-promotes it onto the raids index page).
ALLOWED_TYPE_TOKENS: frozenset[str] = frozenset({"dungeon"})


def _resolve_zone_id_sync(zone_name: str) -> int | None:
    z = zones_db.find_by_name(zone_name)
    return z["id"] if z else None


async def _resolve_zone_id(zone_name: str) -> int:
    zid = await run_sync(_resolve_zone_id_sync, zone_name)
    if zid is None:
        raise HTTPException(status_code=404, detail=f"Zone {zone_name!r} not found")
    return zid


# --- request bodies ----------------------------------------------------------


class EncounterCreateBody(BaseModel):
    primary_mob: str = Field(..., min_length=1)
    position: int | None = None
    stage: str | None = None
    wiki_url: str | None = None


class EncounterUpdateBody(BaseModel):
    primary_mob: str | None = Field(None, min_length=1)
    stage: str | None = None
    wiki_url: str | None = None


class ReorderBody(BaseModel):
    ordered_encounter_ids: list[int] = Field(..., min_length=1)


class MobCreateBody(BaseModel):
    mob_name: str = Field(..., min_length=1)
    make_primary: bool = False


class MobUpdateBody(BaseModel):
    mob_name: str = Field(..., min_length=1)


class ZoneTypeBody(BaseModel):
    type: str = Field(..., min_length=1)


# --- endpoints ---------------------------------------------------------------


@router.post(
    "/zones/{zone_name}/encounters",
    dependencies=[Depends(require_editor)],
)
async def create_encounter(zone_name: str, body: EncounterCreateBody) -> dict:
    zone_id = await _resolve_zone_id(zone_name)
    result = await run_sync(
        lambda: zones_db.add_encounter(
            zone_id=zone_id,
            primary_mob=body.primary_mob,
            position=body.position,
            stage=body.stage,
            wiki_url=body.wiki_url,
        ),
    )
    invalidate_zones_cache()
    return result


@router.put(
    "/zones/{zone_name}/encounters/reorder",
    dependencies=[Depends(require_editor)],
)
async def reorder_zone_encounters(zone_name: str, body: ReorderBody) -> dict:
    zone_id = await _resolve_zone_id(zone_name)
    try:
        await run_sync(
            zones_db.reorder_encounters,
            zone_id,
            body.ordered_encounter_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    invalidate_zones_cache()
    z = await run_sync(zones_db.find_by_name, zone_name)
    return z or {}


@router.put(
    "/zones/{zone_name}/encounters/{encounter_id}",
    dependencies=[Depends(require_editor)],
)
async def edit_encounter(zone_name: str, encounter_id: int, body: EncounterUpdateBody) -> dict:
    await _resolve_zone_id(zone_name)
    # Only forward fields the client actually sent — so the zones_db sentinel
    # default ("leave unchanged") fires for omitted fields and an explicit
    # JSON `null` becomes a real `None` ("clear the column").
    # The typed kwargs dicts avoid Pyright's inability to infer **-merged types.
    sent = body.model_fields_set
    kw_pm: dict[str, str | None] = {"primary_mob": body.primary_mob} if "primary_mob" in sent else {}
    kw_st: dict[str, str | None] = {"stage": body.stage} if "stage" in sent else {}
    kw_wu: dict[str, str | None] = {"wiki_url": body.wiki_url} if "wiki_url" in sent else {}
    # Merge the typed dicts before the executor call to keep the lambda simple.
    merged: dict[str, str | None] = {**kw_pm, **kw_st, **kw_wu}
    try:
        result = await run_sync(
            lambda: zones_db.update_encounter(encounter_id, **merged),  # type: ignore[arg-type]
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    invalidate_zones_cache()
    return result


@router.delete(
    "/zones/{zone_name}/encounters/{encounter_id}",
    status_code=204,
    dependencies=[Depends(require_editor)],
)
async def remove_encounter(zone_name: str, encounter_id: int) -> None:
    await _resolve_zone_id(zone_name)
    ok = await run_sync(zones_db.delete_encounter, encounter_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Encounter not found")
    invalidate_zones_cache()


@router.post(
    "/zones/{zone_name}/encounters/{encounter_id}/mobs",
    dependencies=[Depends(require_editor)],
)
async def create_mob(zone_name: str, encounter_id: int, body: MobCreateBody) -> dict:
    await _resolve_zone_id(zone_name)
    result = await run_sync(
        lambda: zones_db.add_mob(
            encounter_id,
            mob_name=body.mob_name,
            make_primary=body.make_primary,
        ),
    )
    invalidate_zones_cache()
    return result


@router.put(
    "/zones/{zone_name}/encounters/{encounter_id}/mobs/{mob_id}",
    dependencies=[Depends(require_editor)],
)
async def edit_mob(zone_name: str, encounter_id: int, mob_id: int, body: MobUpdateBody) -> dict:
    await _resolve_zone_id(zone_name)
    try:
        result = await run_sync(lambda: zones_db.update_mob(mob_id, mob_name=body.mob_name))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    invalidate_zones_cache()
    return result


@router.post(
    "/zones/{zone_name}/encounters/{encounter_id}/mobs/{mob_id}/promote",
    dependencies=[Depends(require_editor)],
)
async def promote_mob_route(zone_name: str, encounter_id: int, mob_id: int) -> dict:
    await _resolve_zone_id(zone_name)
    try:
        result = await run_sync(zones_db.promote_mob, mob_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    invalidate_zones_cache()
    return result


@router.delete(
    "/zones/{zone_name}/encounters/{encounter_id}/mobs/{mob_id}",
    status_code=204,
    dependencies=[Depends(require_editor)],
)
async def remove_mob(zone_name: str, encounter_id: int, mob_id: int) -> None:
    await _resolve_zone_id(zone_name)
    try:
        ok = await run_sync(zones_db.delete_mob, mob_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Mob not found")
    invalidate_zones_cache()


# --- zone-type tag endpoints (dungeon curation) -----------------------------
# Used by the Dungeons card on /raids (contributors-only on the frontend).
# The backend still gates by require_editor so direct API access stays
# consistent with the encounter editor.


@router.post(
    "/zones/{zone_name}/types",
    dependencies=[Depends(require_editor)],
)
async def add_zone_type_route(zone_name: str, body: ZoneTypeBody) -> dict:
    if body.type not in ALLOWED_TYPE_TOKENS:
        raise HTTPException(
            status_code=400,
            detail=f"type must be one of {sorted(ALLOWED_TYPE_TOKENS)}",
        )
    result = await run_sync(zones_db.add_zone_type, zone_name, body.type)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Zone {zone_name!r} not found")
    invalidate_zones_cache()
    return result


@router.delete(
    "/zones/{zone_name}/types/{type_token}",
    dependencies=[Depends(require_editor)],
)
async def remove_zone_type_route(zone_name: str, type_token: str) -> dict:
    if type_token not in ALLOWED_TYPE_TOKENS:
        raise HTTPException(
            status_code=400,
            detail=f"type must be one of {sorted(ALLOWED_TYPE_TOKENS)}",
        )
    result = await run_sync(zones_db.remove_zone_type, zone_name, type_token)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Zone {zone_name!r} not found")
    invalidate_zones_cache()
    return result
