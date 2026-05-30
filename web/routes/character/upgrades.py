"""GET /character/{name}/upgrade-materials + /upgrade-recipes.

Carved out of the original 933-line web/routes/character.py.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from fastapi import HTTPException, Request
from pydantic import BaseModel

from census.db import DB_PATH as _ITEMS_DB
from census.recipes_db import DB_PATH as _RECIPES_DB
from census.recipes_db import find_spells_by_tier as _find_spell_recipes
from census.spells_db import DB_PATH as _SPELLS_DB
from census.spells_db import SpellRow as _SpellRow
from census.spells_db import find_by_ids as _spell_find_by_ids
from census.spells_db import load_blocklist as _load_spell_blocklist
from census.spells_db import strip_roman as _strip_roman
from census.spells_db import unique_highest_entries as _unique_highest_rows
from web.cache import character_cache
from web.lib.cache_keys import char_cache_key
from web.lib.census_lifecycle import shared_census_client
from web.limiter import limiter
from web.routes.character import router
from web.routes.character.views import _build_char_response
from web.routes.recipes import IngredientResponse as _RecipeIngredientResponse
from web.routes.recipes import RecipeResult as _RecipeResult
from web.routes.recipes import _bench_label as _recipe_bench_label
from web.routes.recipes import _fuel_to_craft_tier as _recipe_fuel_to_craft_tier
from web.server_context import current_world

_SUB_EXPERT_TIERS = {"Apprentice", "Journeyman", "Adept"}


def _lookup_items_by_name(names: list[str]) -> dict[str, dict]:
    """Bulk lookup ingredient names in the local items DB.

    Returns a mapping of lowercased original name → {item_id, icon_id, tier,
    description, item_level}.  Missing items are simply absent from the result.

    Two-pass strategy for "Raw X" ingredients:
      Pass 1 — exact match after stripping "Raw ": "Raw Root" → look for "root".
      Pass 2 — for any "Raw X" still unmatched, do a fuzzy LIKE %X% search
               filtered to no-value items with max_stack_size=800.  This handles
               renamed materials like "Raw Opaline" → "Rough Opaline".
    Non-"Raw" ingredients are only attempted with an exact match.
    """
    if not _ITEMS_DB.exists() or not names:
        return {}

    # Partition into "raw" originals and plain originals.
    # orig_to_lookup maps lowercased original → exact lookup key (Raw stripped).
    orig_to_lookup: dict[str, str] = {}
    raw_originals: list[str] = []  # lowercased originals that started with "raw "
    for n in names:
        lo = n.lower()
        if lo.startswith("raw "):
            stripped = lo[4:]
            orig_to_lookup[lo] = stripped
            raw_originals.append(lo)
        else:
            orig_to_lookup[lo] = lo

    def _row_to_info(row) -> dict:
        tier_raw = row[3] or ""
        return {
            "item_id": row[0],
            "display_name": row[1],  # canonical cased name from DB
            "icon_id": row[2],
            "tier": tier_raw.title() if tier_raw else None,
            "description": row[4] or None,
            "item_level": row[5],
        }

    by_lookup: dict[str, dict] = {}
    with sqlite3.connect(_ITEMS_DB) as conn:
        # ── Pass 1: exact match (displayname_lower IN (...)) ─────────────────
        unique_lookups = list(set(orig_to_lookup.values()))
        placeholders = ",".join("?" * len(unique_lookups))
        rows = conn.execute(
            f"SELECT id, displayname, icon_id, tier_display, description, item_level "
            f"FROM items WHERE displayname_lower IN ({placeholders})",
            unique_lookups,
        ).fetchall()
        for row in rows:
            key = row[1].lower()  # displayname → lowercase for keying
            if key not in by_lookup:
                by_lookup[key] = _row_to_info(row)

        # ── Pass 2: fuzzy fallback for unmatched "Raw X" names ───────────────
        # For each "raw opaline" whose stripped form "opaline" wasn't found,
        # search for no-value items with max_stack_size=800 whose name contains
        # the keyword ("opaline").  Pick the first match (e.g. "Rough Opaline").
        unmatched_raw = [lo for lo in raw_originals if orig_to_lookup[lo] not in by_lookup]
        for lo in unmatched_raw:
            keyword = orig_to_lookup[lo]  # e.g. "opaline"
            row = conn.execute(
                "SELECT id, displayname, icon_id, tier_display, description, item_level "
                "FROM items "
                "WHERE displayname_lower LIKE ? "
                "  AND flag_no_value = 1 "
                "  AND max_stack_size = 800 "
                "LIMIT 1",
                (f"%{keyword}%",),
            ).fetchone()
            if row:
                # Store under the stripped keyword so the mapping below picks it up
                by_lookup[keyword] = _row_to_info(row)

    # Map results back to original ingredient names
    return {orig: by_lookup[lookup] for orig, lookup in orig_to_lookup.items() if lookup in by_lookup}


class IngredientResponse(BaseModel):
    name: str
    quantity: int
    category: str  # "primary" | "secondary" | "fuel"
    item_id: int | None = None
    icon_id: int | None = None
    tier: str | None = None
    description: str | None = None
    item_level: int | None = None


class UpgradeMaterialsResponse(BaseModel):
    spells_needing_upgrade: int  # sub-expert spells found in spell DB
    spells_with_recipe: int  # of those, how many had an Expert recipe
    ingredients: list[IngredientResponse]  # aggregated, sorted qty desc within category


@router.get("/character/{name}/upgrade-materials", response_model=UpgradeMaterialsResponse)
@limiter.limit("20/minute")
async def get_upgrade_materials(request: Request, name: str) -> UpgradeMaterialsResponse:
    """Return the aggregated crafting materials needed to upgrade all sub-Expert
    spells to Expert tier, using the local recipes DB.
    """
    # Graceful degradation if either DB is missing
    if not _SPELLS_DB.exists():
        raise HTTPException(status_code=503, detail="Spells database not available")
    if not _RECIPES_DB.exists():
        raise HTTPException(status_code=503, detail="Recipes database not available")

    # Reuse cached character record
    cache_key = char_cache_key(name, current_world())
    cached, _ = character_cache.get_stale(cache_key)
    if cached is not None:
        spell_ids = cached.spell_ids
    else:
        async with shared_census_client() as client:
            char = await client.get_character(name, current_world())
        if char is None:
            raise HTTPException(status_code=404, detail=f"Character '{name}' not found on {current_world()}")
        result = _build_char_response(char)
        character_cache.set(cache_key, result)
        spell_ids = result.spell_ids

    if not spell_ids:
        return UpgradeMaterialsResponse(spells_needing_upgrade=0, spells_with_recipe=0, ingredients=[])

    # Get spell rows, apply same filter as the spells endpoint
    spell_db: dict[int, _SpellRow] = _spell_find_by_ids(spell_ids)
    blocklist = _load_spell_blocklist()
    rows = [
        r
        for r in spell_db.values()
        if (r.get("level") or 0) > 0
        and r.get("type") in ("spells", "arts")
        and r.get("given_by") == "spellscroll"
        and _strip_roman(r.get("name") or "").lower() not in blocklist
    ]
    rows = _unique_highest_rows(rows)

    # Keep only sub-Expert spells
    sub_expert = [r for r in rows if (r.get("tier_name") or "") in _SUB_EXPERT_TIERS]
    if not sub_expert:
        return UpgradeMaterialsResponse(spells_needing_upgrade=0, spells_with_recipe=0, ingredients=[])

    # Bulk recipe lookup: one DB query for all spell names
    spell_names = [r["name"] for r in sub_expert]
    recipes = _find_spell_recipes(spell_names, "Expert", path=_RECIPES_DB)

    # Aggregate ingredients across all matched recipes
    totals: dict[str, int] = defaultdict(int)
    cats: dict[str, str] = {}

    for recipe in recipes.values():
        pc = recipe.get("primary_comp")
        if pc:
            totals[pc] += recipe.get("primary_qty") or 1
            cats[pc] = "primary"
        for sc in recipe.get("secondary_comps") or []:
            n = sc.get("description") or ""
            if n:
                totals[n] += sc.get("quantity") or 1
                cats[n] = "secondary"
        fc = recipe.get("fuel_comp")
        if fc:
            totals[fc] += recipe.get("fuel_qty") or 1
            cats[fc] = "fuel"

    # Bulk item DB lookup for icons + tooltip data
    item_data = _lookup_items_by_name(list(totals.keys()))

    # Sort: primary first, then secondary, then fuel; within each group by qty desc
    _cat_order = {"primary": 0, "secondary": 1, "fuel": 2}
    ingredients = sorted(
        [
            IngredientResponse(
                name=(item_data.get(n.lower(), {}).get("display_name") or n),
                quantity=q,
                category=cats[n],
                item_id=item_data.get(n.lower(), {}).get("item_id"),
                icon_id=item_data.get(n.lower(), {}).get("icon_id"),
                tier=item_data.get(n.lower(), {}).get("tier"),
                description=item_data.get(n.lower(), {}).get("description"),
                item_level=item_data.get(n.lower(), {}).get("item_level"),
            )
            for n, q in totals.items()
        ],
        key=lambda i: (_cat_order.get(i.category, 9), -i.quantity),
    )

    return UpgradeMaterialsResponse(
        spells_needing_upgrade=len(sub_expert),
        spells_with_recipe=len(recipes),
        ingredients=ingredients,
    )


class UpgradeRecipesResponse(BaseModel):
    results: list[_RecipeResult]
    spells_needing_upgrade: int
    spells_with_recipe: int


@router.get("/character/{name}/upgrade-recipes", response_model=UpgradeRecipesResponse)
@limiter.limit("20/minute")
async def get_upgrade_recipes(request: Request, name: str) -> UpgradeRecipesResponse:
    """Return full recipe objects needed to upgrade all sub-Expert spells to Expert tier.

    The response matches the RecipeResult shape used by the Recipes page so the
    caller can write the list directly into the shopping-list localStorage entry.
    """
    if len(name) > 64:
        raise HTTPException(status_code=400, detail="Character name is too long")
    if not _SPELLS_DB.exists():
        raise HTTPException(status_code=503, detail="Spells database not available")
    if not _RECIPES_DB.exists():
        raise HTTPException(status_code=503, detail="Recipes database not available")

    # Reuse cached character record (same pattern as get_upgrade_materials)
    cache_key = char_cache_key(name, current_world())
    cached, _ = character_cache.get_stale(cache_key)
    if cached is not None:
        spell_ids = cached.spell_ids
    else:
        async with shared_census_client() as client:
            char = await client.get_character(name, current_world())
        if char is None:
            raise HTTPException(status_code=404, detail=f"Character '{name}' not found on {current_world()}")
        result = _build_char_response(char)
        character_cache.set(cache_key, result)
        spell_ids = result.spell_ids

    if not spell_ids:
        return UpgradeRecipesResponse(results=[], spells_needing_upgrade=0, spells_with_recipe=0)

    # Get spell rows, apply same filter as the spells endpoint
    spell_db: dict[int, _SpellRow] = _spell_find_by_ids(spell_ids)
    blocklist = _load_spell_blocklist()
    rows = [
        r
        for r in spell_db.values()
        if (r.get("level") or 0) > 0
        and r.get("type") in ("spells", "arts")
        and r.get("given_by") == "spellscroll"
        and _strip_roman(r.get("name") or "").lower() not in blocklist
    ]
    rows = _unique_highest_rows(rows)

    sub_expert = [r for r in rows if (r.get("tier_name") or "") in _SUB_EXPERT_TIERS]
    if not sub_expert:
        return UpgradeRecipesResponse(results=[], spells_needing_upgrade=0, spells_with_recipe=0)

    # Bulk recipe lookup
    spell_names = [r["name"] for r in sub_expert]
    recipes = _find_spell_recipes(spell_names, "Expert", path=_RECIPES_DB)

    results = [
        _RecipeResult(
            id=recipe["id"],
            name=recipe["name"],
            bench=recipe.get("bench"),
            bench_label=_recipe_bench_label(recipe.get("bench")),
            craft_tier=_recipe_fuel_to_craft_tier(recipe.get("fuel_comp")),
            crafted_tier=recipe.get("crafted_tier"),
            primary_comp=recipe.get("primary_comp"),
            primary_qty=recipe.get("primary_qty"),
            secondary_comps=[
                _RecipeIngredientResponse(
                    description=sc.get("description", ""),
                    quantity=sc.get("quantity", 1),
                )
                for sc in (recipe.get("secondary_comps") or [])
                if sc.get("description")
            ],
            fuel_comp=recipe.get("fuel_comp"),
            fuel_qty=recipe.get("fuel_qty"),
            out_formed_id=recipe.get("out_formed_id"),
            out_formed_count=recipe.get("out_formed_count"),
            class_label=None,
        )
        for recipe in recipes.values()
    ]

    return UpgradeRecipesResponse(
        results=results,
        spells_needing_upgrade=len(sub_expert),
        spells_with_recipe=len(recipes),
    )
