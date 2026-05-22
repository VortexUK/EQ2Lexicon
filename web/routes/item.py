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
# Class-label decomposition
# ---------------------------------------------------------------------------
# Full archetypes first so they are preferred over their constituent parts.
# This mirrors the ordered list in census/db.py (_ARCHETYPES) but uses the
# display names that ItemData.classes carries (title-case, from Census API).
_ARCHETYPE_DECOMP: list[tuple[frozenset, str]] = [
    # ── Full archetypes ──────────────────────────────────────────────────────
    (frozenset(["Guardian","Berserker","Monk","Bruiser","Shadowknight","Paladin"]),
     "All Fighters"),
    (frozenset(["Templar","Inquisitor","Fury","Warden","Mystic","Defiler","Channeler"]),
     "All Priests"),
    (frozenset(["Troubador","Dirge","Assassin","Ranger","Swashbuckler","Brigand","Beastlord"]),
     "All Scouts"),
    (frozenset(["Coercer","Illusionist","Conjuror","Necromancer","Wizard","Warlock"]),
     "All Mages"),
    # ── Sub-archetypes ───────────────────────────────────────────────────────
    (frozenset(["Guardian",     "Berserker"]),      "All Warriors"),
    (frozenset(["Shadowknight", "Paladin"]),         "All Crusaders"),
    (frozenset(["Monk",         "Bruiser"]),         "All Brawlers"),
    (frozenset(["Templar",      "Inquisitor"]),      "All Clerics"),
    (frozenset(["Fury",         "Warden"]),          "All Druids"),
    (frozenset(["Mystic",       "Defiler"]),         "All Shamans"),
    (frozenset(["Troubador",    "Dirge"]),           "All Bards"),
    (frozenset(["Assassin",     "Ranger"]),          "All Predators"),
    (frozenset(["Swashbuckler", "Brigand"]),         "All Rogues"),
    (frozenset(["Coercer",      "Illusionist"]),     "All Enchanters"),
    (frozenset(["Conjuror",     "Necromancer"]),     "All Summoners"),
    (frozenset(["Wizard",       "Warlock"]),         "All Sorcerers"),
]


# ---------------------------------------------------------------------------
# Filter normalisation constants
# ---------------------------------------------------------------------------

# Canonical tier display names, ordered highest → lowest quality.
# DB stores tiers in ALL-CAPS; _TIER_DB_MAP converts them for display.
_CANONICAL_TIERS = [
    "Celestial", "Ethereal", "Mythical", "Fabled",
    "Legendary", "Treasured", "Uncommon",
    "Mastercrafted", "Handcrafted", "Common",
]
_TIER_ORDER_IDX  = {t: i for i, t in enumerate(_CANONICAL_TIERS)}
_TIER_DB_MAP     = {t.upper(): t for t in _CANONICAL_TIERS}  # e.g. "FABLED" → "Fabled"

# typeinfo_name values to exclude from the item-type filter
_ITEM_TYPE_SKIP = frozenset(["spellscroll", "recipescroll", "coinpurse", "equipmentinfuser"])

# typeinfo_name raw → display name (renames + merges)
_ITEM_TYPE_RENAME = {
    "houseitem":     "House Item",
    "itemcontainer": "Container",   # merged with 'container'
    "itempattern":   "Pattern",
}

# display name → list of raw DB typeinfo_name values (for search)
_ITEM_TYPE_DB_MAP: dict[str, list[str]] = {
    "Container": ["container", "itemcontainer"],
    "House Item": ["houseitem"],
    "Pattern":   ["itempattern", "pattern"],
}

# Slot names to suppress (mount/horse equipment, not applicable on TLE)
_SLOT_SKIP = frozenset([
    "Barding", "Breeching", "Hackamore", "Reins",
    "Saddle", "Shoes", "Stirrup", "Textures",
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_classes(classes: list[str]) -> str:
    """Collapse a class list into a human-readable label (mirrors image/tooltip.py)."""
    if not classes:
        return ""
    class_set = frozenset(classes)
    # Exact match first (handles single archetypes, All Classes, etc.)
    match = CLASS_GROUPS.get(class_set)
    if match:
        return match
    # Greedy decomposition: full archetypes first, then sub-archetypes.
    # _ARCHETYPE_DECOMP is ordered largest → smallest so larger groups are
    # consumed before their constituent sub-groups.
    remaining: set[str] = set(class_set)
    matched: list[str] = []
    for archetype_set, archetype_name in _ARCHETYPE_DECOMP:
        if archetype_set <= remaining:
            matched.append(archetype_name)
            remaining -= archetype_set
    if not remaining:
        return " / ".join(matched)
    # Some classes didn't fit any group — append them individually
    if matched:
        return " / ".join(matched + sorted(remaining))
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


class SetBonusResponse(BaseModel):
    required_items: int
    effect: str
    lines: list[str]


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
    set_name: str | None = None
    set_bonuses: list[SetBonusResponse] = []


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
    stat_values: dict[str, float] = {}   # stat_name → value for each has_stat filter


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
    server_max_level: int | None = None


def _get_server_max_level() -> int | None:
    """Read SERVER_MAX_LEVEL at request time so dotenv is always loaded first."""
    raw = os.getenv("SERVER_MAX_LEVEL", "").strip()
    return int(raw) if raw.isdigit() else None


@router.get("/items/filters", response_model=ItemFilterOptions)
async def get_item_filters() -> ItemFilterOptions:
    """Return distinct tier / slot / type values for filter dropdowns."""
    server_max_level = _get_server_max_level()
    if not DB_PATH.exists():
        return ItemFilterOptions(tiers=[], slots=[], item_types=[], server_max_level=server_max_level)

    async with aiosqlite.connect(DB_PATH) as db:
        # ── Tiers ──────────────────────────────────────────────────────────
        # Map raw ALL-CAPS DB values to canonical display names; skip
        # compound tiers like "MASTERCRAFTED FABLED"; sort by quality order.
        async with db.execute(
            "SELECT DISTINCT tier_display FROM items "
            "WHERE visible=1 AND tier_display IS NOT NULL"
        ) as cur:
            raw_tiers = [r[0] for r in await cur.fetchall()]

        seen_tiers: set[str] = set()
        tiers: list[str] = []
        for raw in raw_tiers:
            display = _TIER_DB_MAP.get(raw)   # only single canonical tiers match
            if display and display not in seen_tiers:
                seen_tiers.add(display)
                tiers.append(display)
        tiers.sort(key=lambda t: _TIER_ORDER_IDX.get(t, 99))

        # ── Slots ──────────────────────────────────────────────────────────
        async with db.execute(
            "SELECT DISTINCT slot FROM items "
            "WHERE visible=1 AND slot IS NOT NULL "
            "ORDER BY slot"
        ) as cur:
            slots = [r[0] for r in await cur.fetchall() if r[0] not in _SLOT_SKIP]

        # ── Item types ─────────────────────────────────────────────────────
        # Rename / skip / capitalise raw typeinfo_name values; deduplicate.
        async with db.execute(
            "SELECT DISTINCT typeinfo_name FROM items "
            "WHERE visible=1 AND typeinfo_name IS NOT NULL"
        ) as cur:
            raw_types = [r[0] for r in await cur.fetchall()]

        seen_types: set[str] = set()
        for raw in raw_types:
            if raw in _ITEM_TYPE_SKIP:
                continue
            display = _ITEM_TYPE_RENAME.get(raw)
            if display is None:
                # Capitalise first letter, leave rest as-is
                display = raw[0].upper() + raw[1:] if raw else raw
            if display not in seen_types:
                seen_types.add(display)
        item_types = sorted(seen_types)

    return ItemFilterOptions(
        tiers=tiers,
        slots=slots,
        item_types=item_types,
        server_max_level=server_max_level,
    )


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
    stat_filter: list[str]            = Query(default=[]),  # "StatName" or "StatName:gte:50" or "StatName:lte:50"
    sort_by:    str                   = "name",  # name | level | tier
    sort_dir:   str                   = "asc",
    page:       int                   = 1,
) -> ItemSearchResponse:
    """
    Search the local items DB with optional filters.
    At least one filter must be provided.

    stat_filter entries are encoded as:
      "StatName"           – item must have the stat (any value)
      "StatName:gte:50"    – stat value >= 50
      "StatName:lte:50"    – stat value <= 50
    """
    per_page = 50

    # Parse stat_filter entries into (stat_name, op, threshold_or_None) tuples
    parsed_stats: list[tuple[str, str, float | None]] = []
    for sf in stat_filter:
        parts = sf.split(":", 2)
        if len(parts) == 3:
            sname, op, val_str = parts
            op = op.lower()
            if op not in ("gte", "lte"):
                op = "gte"
            try:
                parsed_stats.append((sname, op, float(val_str)))
            except ValueError:
                parsed_stats.append((sname, "gte", None))
        else:
            parsed_stats.append((sf, "gte", None))

    has_stat = [s for s, _, __ in parsed_stats]  # plain name list for compat

    # Require at least one meaningful filter
    if not any([name, tier, slot, item_type, class_name,
                min_level is not None, max_level is not None, parsed_stats]):
        return ItemSearchResponse(results=[], total=0, page=1, per_page=per_page)

    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="Item database not available")

    # ── Build WHERE clause ────────────────────────────────────────────────────
    conditions: list[str] = ["i.visible = 1", "i.flag_pvp = 0"]
    where_params: list = []  # bound to the WHERE clause

    if name:
        conditions.append("i.displayname_lower LIKE ?")
        where_params.append(f"%{name.lower()}%")

    if tier:
        # Frontend sends the canonical display name (e.g. "Fabled").
        # Convert to uppercase to match the DB, then use LIKE so that
        # compound tiers ("MASTERCRAFTED FABLED") are included.
        # Exception: "COMMON" must be an exact match to avoid matching "UNCOMMON".
        db_tier = tier.upper()
        if db_tier == "COMMON":
            conditions.append("i.tier_display = ?")
            where_params.append("COMMON")
        else:
            conditions.append("i.tier_display LIKE ?")
            where_params.append(f"%{db_tier}%")

    if slot:
        conditions.append("i.slot = ?")
        where_params.append(slot)

    if item_type:
        # Map display name back to raw DB typeinfo_name value(s)
        raw_types = _ITEM_TYPE_DB_MAP.get(item_type)
        if raw_types:
            placeholders = ",".join("?" * len(raw_types))
            conditions.append(f"LOWER(i.typeinfo_name) IN ({placeholders})")
            where_params.extend(raw_types)          # already lowercase
        else:
            # General case: compare lowercase (DB values are all-lowercase)
            conditions.append("LOWER(i.typeinfo_name) = ?")
            where_params.append(item_type.lower())

    if class_name:
        # classes_json is a JSON object keyed by lowercase class name
        conditions.append("LOWER(i.classes_json) LIKE ?")
        where_params.append(f'%"{class_name.lower()}"%')

    if min_level is not None:
        conditions.append("i.level_to_use >= ?")
        where_params.append(min_level)

    if max_level is not None:
        conditions.append("i.level_to_use <= ?")
        where_params.append(max_level)

    where = " AND ".join(conditions)

    # ── Stats JOINs (one per required stat) ──────────────────────────────────
    # IMPORTANT: JOIN clauses appear *before* WHERE in the SQL string, so their
    # bound parameters must also come first in the params tuple.  We collect them
    # in a separate list and prepend to where_params when building the final
    # params tuple (see `params = join_params + where_params` below).
    stat_alias: dict[str, str] = {}
    stat_joins = ""
    join_params: list = []  # bound to ON conditions inside each JOIN

    for i, (stat, op, threshold) in enumerate(parsed_stats):
        alias = f"s{i}"
        stat_alias[stat] = alias
        if threshold is not None:
            op_sql = ">=" if op == "gte" else "<="
            stat_joins += (
                f" JOIN item_stats {alias} ON i.id = {alias}.item_id"
                f" AND {alias}.stat = ? AND {alias}.value {op_sql} ?"
            )
            join_params.extend([stat, threshold])
        else:
            stat_joins += f" JOIN item_stats {alias} ON i.id = {alias}.item_id AND {alias}.stat = ?"
            join_params.append(stat)

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
            join_params.append(sort_by)
            sort_stat_col = "ssort.value"
        order_clause = f"COALESCE({sort_stat_col}, 0) {direction}, i.displayname_lower ASC"

    # JOIN params precede WHERE params because JOINs appear before WHERE in SQL
    params = join_params + where_params

    offset = (page - 1) * per_page

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Total count  (use subquery to avoid DISTINCT issues with LEFT JOINs)
        count_sql = (
            f"SELECT COUNT(DISTINCT i.id) FROM items i{stat_joins} WHERE {where}"
        )
        async with db.execute(count_sql, params) as cur:
            total = (await cur.fetchone())[0]

        # SELECT the value for each has_stat filter — INNER JOINs guarantee
        # non-NULL values, so no COALESCE needed here.
        stat_val_selects = "".join(
            f", {stat_alias[stat]}.value AS _sv_{stat_alias[stat]}"
            for stat in has_stat
        )
        select_sql = (
            f"SELECT i.id, i.displayname, i.tier_display, i.slot, "
            f"i.typeinfo_name, i.level_to_use, i.class_label, i.icon_id"
            f"{stat_val_selects} "
            f"FROM items i{stat_joins} "
            f"WHERE {where} "
            f"GROUP BY i.id "
            f"ORDER BY {order_clause} "
            f"LIMIT {per_page} OFFSET {offset}"
        )
        async with db.execute(select_sql, params) as cur:
            rows = await cur.fetchall()

        # For each result, fetch its stat names and build the stat_values map
        results: list[ItemSearchResult] = []
        for row in rows:
            item_id = row["id"]
            async with db.execute(
                "SELECT stat FROM item_stats WHERE item_id = ? ORDER BY stat",
                (item_id,),
            ) as scur:
                stat_names = [r[0] for r in await scur.fetchall()]

            stat_vals: dict[str, float] = {
                stat: float(row[f"_sv_{stat_alias[stat]}"])
                for stat in has_stat
                if row[f"_sv_{stat_alias[stat]}"] is not None
            }

            results.append(ItemSearchResult(
                id          = item_id,
                name        = row["displayname"],
                tier        = row["tier_display"],
                slot        = row["slot"],
                item_type   = row["typeinfo_name"],
                level       = row["level_to_use"],
                class_label = row["class_label"],
                icon_id     = row["icon_id"],
                stat_values = stat_vals,
                stats       = stat_names,
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
        set_name=item.set_name,
        set_bonuses=[
            SetBonusResponse(
                required_items=b.required_items,
                effect=b.effect,
                lines=b.lines,
            )
            for b in item.set_bonuses
        ],
    )
