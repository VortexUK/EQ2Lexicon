"""Write endpoints for the per-zone raid boss roster — add/edit/delete/reorder
encounters and add/edit/promote/delete mobs within an encounter. All gated by
require_editor (admin OR contributor). Reads still live in web/routes/zones.py;
this sibling file keeps the read/write split clean."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from census import zones_db
from web.auth_deps import require_admin, require_editor
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


class ExpansionEntry(BaseModel):
    """One expansion as returned by the featured-raid endpoints. ``name``
    and ``year`` come straight from the zones table (may be NULL for
    pre-launch or historically-mis-tagged rows)."""

    short: str
    name: str | None = None
    year: int | None = None


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


# --- featured raid expansions (admin-curated /raids page list) --------------
# Stricter auth than the dungeon endpoints above: raid curation is
# admin-only. The /raids landing page reads from the *public* list endpoint
# (no auth) and the admin gates only the available-list + write endpoints.


@router.get("/raids/expansions", response_model=list[ExpansionEntry])
async def list_raid_expansions() -> list[dict]:
    """Public read of the admin-curated raid expansion list. Used by the
    /raids page to render its sections."""
    return await run_sync(zones_db.list_featured_raid_expansions)


@router.get(
    "/raids/expansions/available",
    response_model=list[ExpansionEntry],
    dependencies=[Depends(require_admin)],
)
async def list_raid_expansions_available() -> list[dict]:
    """Admin-only: expansions in zones.db NOT yet featured. For the
    'Add expansion' picker."""
    return await run_sync(zones_db.list_available_raid_expansions)


@router.post(
    "/raids/expansions/{expansion_short}",
    dependencies=[Depends(require_admin)],
)
async def add_raid_expansion(expansion_short: str) -> dict:
    """Admin-only: mark an expansion as featured. Empty by default — admin
    must subsequently 'Add raid zone' to populate it."""
    ok = await run_sync(zones_db.add_featured_raid_expansion, expansion_short)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Expansion {expansion_short!r} not found in zones.db",
        )
    invalidate_zones_cache()
    return {"expansion_short": expansion_short}


@router.delete(
    "/raids/expansions/{expansion_short}",
    dependencies=[Depends(require_admin)],
)
async def remove_raid_expansion(expansion_short: str) -> dict:
    """Admin-only: remove an expansion from featured AND cascade-remove all
    featured raid zones under it. The underlying zone_encounters (curated
    bosses) are preserved — re-adding the expansion + zones restores them."""
    removed = await run_sync(zones_db.remove_featured_raid_expansion, expansion_short)
    invalidate_zones_cache()
    return {"expansion_short": expansion_short, "removed": removed}


# --- featured raid zones (admin-curated raid roster within an expansion) ----


@router.get("/raids/zones", response_model=list[dict])
async def list_raid_zones(expansion: str) -> list[dict]:
    """Public read of admin-featured raid zones for an expansion."""
    return await run_sync(zones_db.list_featured_raid_zones, expansion)


@router.get(
    "/raids/zones/available",
    response_model=list[dict],
    dependencies=[Depends(require_admin)],
)
async def list_raid_zones_available(expansion: str) -> list[dict]:
    """Admin-only: zones in the expansion tagged raid_x4 or raid_x2 but
    NOT yet featured. For the 'Add raid zone' picker."""
    return await run_sync(zones_db.list_available_raid_zones, expansion)


@router.post(
    "/raids/zones/{zone_name}",
    dependencies=[Depends(require_admin)],
)
async def add_raid_zone(zone_name: str) -> dict:
    """Admin-only: mark a raid zone as featured for /raids. Requires the
    zone to be tagged raid_x4 or raid_x2 in zones.db."""
    zone = await run_sync(zones_db.add_featured_raid_zone, zone_name)
    if zone is None:
        raise HTTPException(
            status_code=400,
            detail=f"Zone {zone_name!r} not found or not tagged raid_x4/raid_x2",
        )
    invalidate_zones_cache()
    return zone


@router.delete(
    "/raids/zones/{zone_name}",
    dependencies=[Depends(require_admin)],
)
async def remove_raid_zone(zone_name: str) -> dict:
    """Admin-only: remove a raid zone from featured. Underlying
    zone_encounters boss data is preserved."""
    removed = await run_sync(zones_db.remove_featured_raid_zone, zone_name)
    invalidate_zones_cache()
    return {"zone_name": zone_name, "removed": removed}


# --- drag-reorder + categories ---------------------------------------------
# Lanes (categories) are admin-defined ordering rows; zones within an
# expansion are positioned both within their (NULL or named) lane and across
# lanes. The reorder endpoints rewrite the world atomically — they take the
# full ordering, not deltas, so a stale client can't corrupt position state.


class ZoneOrderingEntry(BaseModel):
    """One zone in a reorder payload. `category` of None means the implicit
    'Uncategorised' lane (always pinned at the top of the page)."""

    name: str = Field(..., min_length=1)
    category: str | None = None
    position: int


class ZonesReorderBody(BaseModel):
    expansion: str = Field(..., min_length=1)
    zones: list[ZoneOrderingEntry]


class CategoryOrderingEntry(BaseModel):
    name: str = Field(..., min_length=1)
    position: int


class CategoriesReorderBody(BaseModel):
    expansion: str = Field(..., min_length=1)
    categories: list[CategoryOrderingEntry]


@router.put(
    "/raids/zones/reorder",
    response_model=dict,
    dependencies=[Depends(require_admin)],
)
async def reorder_raid_zones(body: ZonesReorderBody) -> dict:
    """Admin-only: atomic reorder + recategorize of featured raid zones in
    an expansion. Auto-creates missing featured_raid_categories rows for
    any category name not yet tracked, so an admin can drag a zone into a
    fresh lane in a single operation."""
    ok = await run_sync(
        zones_db.reorder_featured_raid_zones,
        body.expansion,
        [e.model_dump() for e in body.zones],
    )
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="One or more zones not found in featured set for this expansion",
        )
    invalidate_zones_cache()
    return {"expansion": body.expansion, "reordered": len(body.zones)}


@router.put(
    "/raids/categories/reorder",
    response_model=dict,
    dependencies=[Depends(require_admin)],
)
async def reorder_raid_categories(body: CategoriesReorderBody) -> dict:
    """Admin-only: atomic position rewrite for category lanes in an
    expansion. Every category in `categories` must already exist (was
    created on first use via reorder_raid_zones)."""
    ok = await run_sync(
        zones_db.reorder_featured_raid_categories,
        body.expansion,
        [e.model_dump() for e in body.categories],
    )
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="One or more categories not found for this expansion",
        )
    invalidate_zones_cache()
    return {"expansion": body.expansion, "reordered": len(body.categories)}


@router.get("/raids/categories", response_model=list[dict])
async def list_raid_categories(expansion: str) -> list[dict]:
    """Public read of category ordering for an expansion. Used by the
    frontend ExpansionSection to render lane headers in their saved
    order; NULL-category zones go in an implicit 'Uncategorised' lane
    pinned at the top of every expansion."""
    return await run_sync(zones_db.list_featured_raid_categories, expansion)


class CreateCategoryBody(BaseModel):
    name: str  # 1-64 chars, will be trimmed; reject if empty after trim


@router.post("/raids/categories", response_model=dict, dependencies=[Depends(require_admin)])
async def create_raid_category(expansion: str, body: CreateCategoryBody) -> dict:
    """Admin-only: create an empty category lane in an expansion. The new
    lane appears at MAX+1 position and starts with zero zones; admin then
    drags zones into it."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Category name must not be empty")
    if len(name) > 64:
        raise HTTPException(status_code=400, detail="Category name must be 64 chars or fewer")
    created = await run_sync(zones_db.create_featured_raid_category, expansion, name)
    if not created:
        raise HTTPException(status_code=409, detail=f"Category {name!r} already exists in {expansion}")
    invalidate_zones_cache()
    return {"expansion": expansion, "name": name}


@router.delete("/raids/categories", response_model=dict, dependencies=[Depends(require_admin)])
async def delete_raid_category(expansion: str, name: str) -> dict:
    """Admin-only: delete a category. Zones currently in this category
    are moved to the Uncategorised lane (category=NULL) — their boss data
    and featured status are preserved."""
    removed = await run_sync(zones_db.delete_featured_raid_category, expansion, name)
    invalidate_zones_cache()
    return {"expansion": expansion, "name": name, "removed": removed}
