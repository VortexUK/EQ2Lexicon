from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from census.client import CensusClient
from census.constants import ARCHETYPES, CLASS_GROUPS
from census.db import DB_PATH

router = APIRouter(tags=["item"])
_SERVICE_ID = os.getenv("CENSUS_SERVICE_ID", "example")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_classes(classes: list[str]) -> str:
    """Collapse a class list into a human-readable label (mirrors image/tooltip.py)."""
    if not classes:
        return ""
    class_set = frozenset(classes)
    for group_set, group_name in CLASS_GROUPS.items():
        if class_set == group_set:
            return group_name
    remaining = class_set
    matched: list[str] = []
    for archetype_set, archetype_name in ARCHETYPES:
        if archetype_set <= remaining:
            matched.append(archetype_name)
            remaining -= archetype_set
    if not remaining and matched:
        return ", ".join(matched)
    return ", ".join(sorted(classes))


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class ItemStatResponse(BaseModel):
    display_name: str
    value: float
    stat_group: str


class EffectLineResponse(BaseModel):
    indentation: int
    text: str


class ItemEffectResponse(BaseModel):
    name: str
    trigger: str
    lines: list[EffectLineResponse]


class ItemResponse(BaseModel):
    id: str
    name: str
    quality: str
    description: str = ""
    icon_id: str | None = None
    slot_type: str = ""
    armor_type: str = ""
    mitigation: int | None = None
    item_level: int | None = None
    required_level: int | None = None
    container_slots: int | None = None
    classes_label: str = ""
    stats: list[ItemStatResponse] = []
    effects: list[ItemEffectResponse] = []
    adornment_slots: list[str] = []
    flags: list[str] = []
    extra_info: list[tuple[str, str]] = []


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Item search models
# ---------------------------------------------------------------------------

class ItemSearchResult(BaseModel):
    id: int
    name: str
    tier: str | None = None
    slot: str | None = None
    item_type: str | None = None
    level: int | None = None
    class_label: str | None = None
    icon_id: int | None = None
    stats: list[str] = []          # canonical stat names present on this item
    sort_stat_value: float | None = None  # value of the sort stat (when sorting by stat)


class ItemSearchResponse(BaseModel):
    results: list[ItemSearchResult]
    total: int
    page: int
    per_page: int


# ---------------------------------------------------------------------------
# Item filter options (distinct values for dropdowns)
# ---------------------------------------------------------------------------

class ItemFilterOptions(BaseModel):
    tiers: list[str]
    slots: list[str]
    item_types: list[str]


@router.get("/items/filters", response_model=ItemFilterOptions)
async def get_item_filters() -> ItemFilterOptions:
    """Return distinct tier / slot / type values for filter dropdowns."""
    if not DB_PATH.exists():
        return ItemFilterOptions(tiers=[], slots=[], item_types=[])

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT tier_display FROM items "
            "WHERE visible=1 AND tier_display IS NOT NULL "
            "ORDER BY tier_display"
        ) as cur:
            tiers = [r[0] for r in await cur.fetchall()]

        async with db.execute(
            "SELECT DISTINCT slot FROM items "
            "WHERE visible=1 AND slot IS NOT NULL "
            "ORDER BY slot"
        ) as cur:
            slots = [r[0] for r in await cur.fetchall()]

        async with db.execute(
            "SELECT DISTINCT typeinfo_name FROM items "
            "WHERE visible=1 AND typeinfo_name IS NOT NULL "
            "ORDER BY typeinfo_name"
        ) as cur:
            item_types = [r[0] for r in await cur.fetchall()]

    return ItemFilterOptions(tiers=tiers, slots=slots, item_types=item_types)


# ---------------------------------------------------------------------------
# Item search endpoint
# ---------------------------------------------------------------------------

@router.get("/items/search", response_model=ItemSearchResponse)
async def search_items(
    name:       str | None            = None,
    tier:       str | None            = None,   # exact tier_display match
    slot:       str | None            = None,   # exact slot match
    item_type:  str | None            = None,   # typeinfo_name LIKE
    class_name: str | None            = None,   # lowercase class key in classes_json
    min_level:  int | None            = None,
    max_level:  int | None            = None,
    has_stat:   list[str]             = Query(default=[]),  # canonical stat names
    sort_by:    str                   = "name",  # name | level | tier
    sort_dir:   str                   = "asc",
    page:       int                   = 1,
) -> ItemSearchResponse:
    """
    Search the local items DB with optional filters.
    At least one filter must be provided.
    """
    per_page = 50

    # Require at least one meaningful filter
    if not any([name, tier, slot, item_type, class_name,
                min_level is not None, max_level is not None, has_stat]):
        return ItemSearchResponse(results=[], total=0, page=1, per_page=per_page)

    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Item database not available")

    # ── Build WHERE clause ────────────────────────────────────────────────────
    conditions: list[str] = ["i.visible = 1"]
    params: list = []

    if name:
        conditions.append("i.displayname_lower LIKE ?")
        params.append(f"%{name.lower()}%")

    if tier:
        conditions.append("i.tier_display = ?")
        params.append(tier)

    if slot:
        conditions.append("i.slot = ?")
        params.append(slot)

    if item_type:
        conditions.append("i.typeinfo_name LIKE ?")
        params.append(f"%{item_type}%")

    if class_name:
        # classes_json is a JSON object keyed by lowercase class name
        conditions.append("LOWER(i.classes_json) LIKE ?")
        params.append(f'%"{class_name.lower()}"%')

    if min_level is not None:
        conditions.append("i.level_to_use >= ?")
        params.append(min_level)

    if max_level is not None:
        conditions.append("i.level_to_use <= ?")
        params.append(max_level)

    where = " AND ".join(conditions)

    # ── Stats JOINs (one per required stat) ──────────────────────────────────
    # Build an index of stat_name → alias for potential reuse when sorting
    stat_alias: dict[str, str] = {}
    stat_joins = ""
    for i, stat in enumerate(has_stat):
        alias = f"s{i}"
        stat_alias[stat] = alias
        stat_joins += f" JOIN item_stats {alias} ON i.id = {alias}.item_id AND {alias}.stat = ?"
        params.append(stat)

    # ── Sort ──────────────────────────────────────────────────────────────────
    direction = "DESC" if sort_dir == "desc" else "ASC"
    sort_stat_col: str | None = None  # SQL expression for stat value when sorting by stat

    _FIXED_SORT = {"level": "i.level_to_use", "tier": "i.tierid", "name": "i.displayname_lower"}

    if sort_by in _FIXED_SORT:
        order_clause = f"{_FIXED_SORT[sort_by]} {direction}, i.displayname_lower ASC"
    else:
        # sort_by is a canonical stat name — JOIN item_stats for it
        if sort_by in stat_alias:
            # Reuse existing INNER JOIN alias — value is already accessible
            sort_stat_col = f"{stat_alias[sort_by]}.value"
        else:
            # Add a LEFT JOIN so items without the stat still appear (sorted last)
            stat_joins += " LEFT JOIN item_stats ssort ON i.id = ssort.item_id AND ssort.stat = ?"
            params.append(sort_by)
            sort_stat_col = "ssort.value"
        order_clause = f"COALESCE({sort_stat_col}, 0) {direction}, i.displayname_lower ASC"

    offset = (page - 1) * per_page

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Total count  (use subquery to avoid DISTINCT issues with LEFT JOINs)
        count_sql = (
            f"SELECT COUNT(DISTINCT i.id) FROM items i{stat_joins} WHERE {where}"
        )
        async with db.execute(count_sql, params) as cur:
            total = (await cur.fetchone())[0]

        # Paged results — include sort stat value so frontend can display it
        sort_val_select = f", COALESCE({sort_stat_col}, 0) AS _sort_val" if sort_stat_col else ""
        select_sql = (
            f"SELECT i.id, i.displayname, i.tier_display, i.slot, "
            f"i.typeinfo_name, i.level_to_use, i.class_label, i.icon_id"
            f"{sort_val_select} "
            f"FROM items i{stat_joins} "
            f"WHERE {where} "
            f"GROUP BY i.id "
            f"ORDER BY {order_clause} "
            f"LIMIT {per_page} OFFSET {offset}"
        )
        async with db.execute(select_sql, params) as cur:
            rows = await cur.fetchall()

        # For each result, fetch its stat names
        results: list[ItemSearchResult] = []
        for row in rows:
            item_id = row["id"]
            async with db.execute(
                "SELECT stat FROM item_stats WHERE item_id = ? ORDER BY stat",
                (item_id,),
            ) as scur:
                stat_names = [r[0] for r in await scur.fetchall()]

            sort_val = float(row["_sort_val"]) if sort_stat_col and row["_sort_val"] else None

            results.append(ItemSearchResult(
                id              = item_id,
                name            = row["displayname"],
                tier            = row["tier_display"],
                slot            = row["slot"],
                item_type       = row["typeinfo_name"],
                level           = row["level_to_use"],
                class_label     = row["class_label"],
                icon_id         = row["icon_id"],
                sort_stat_value = sort_val,
                stats      = stat_names,
            ))

    return ItemSearchResponse(
        results=results,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/item/{item_id}", response_model=ItemResponse)
async def get_item(item_id: str) -> ItemResponse:
    """Return full item detail — local DB first, falls back to Census API if missing."""
    try:
        int(item_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Item ID must be numeric")

    client = CensusClient(service_id=_SERVICE_ID)
    try:
        item = await client.get_item(item_id)
    finally:
        await client.close()

    if item is None:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")

    return ItemResponse(
        id=item.id,
        name=item.name,
        quality=item.quality,
        description=item.description or "",
        icon_id=item.icon_id,
        slot_type=item.slot_type,
        armor_type=item.armor_type,
        mitigation=item.mitigation,
        item_level=item.item_level,
        required_level=item.required_level,
        container_slots=item.container_slots,
        classes_label=_format_classes(item.classes),
        stats=[
            ItemStatResponse(
                display_name=s.display_name,
                value=s.value,
                stat_group=s.stat_group,
            )
            for s in item.stats
        ],
        effects=[
            ItemEffectResponse(
                name=e.name,
                trigger=e.trigger,
                lines=[EffectLineResponse(indentation=ln[0], text=ln[1]) for ln in e.lines],
            )
            for e in item.effects
        ],
        adornment_slots=item.adornment_slots,
        flags=item.flags,
        extra_info=item.extra_info,
    )
