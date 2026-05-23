from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections import Counter, defaultdict

_log = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from census.client import CensusClient
from census.constants import SPELL_TIER_ORDER as _TIER_ORDER
from census.models import AdornSlot as _AdornSlot
from census.spells_db import (
    DB_PATH as _SPELLS_DB,
    find_by_ids as _spell_find_by_ids,
    load_blocklist as _load_spell_blocklist,
    strip_roman as _strip_roman,
    unique_highest_entries as _unique_highest_rows,
)
from census.recipes_db import (
    DB_PATH as _RECIPES_DB,
    find_spells_by_tier as _find_spell_recipes,
)
from web.routes.recipes import (
    RecipeResult as _RecipeResult,
    IngredientResponse as _RecipeIngredientResponse,
    _bench_label as _recipe_bench_label,
    _fuel_to_craft_tier as _recipe_fuel_to_craft_tier,
)
from census.db import DB_PATH as _ITEMS_DB
from web.cache import character_cache
from web.config import SERVICE_ID as _SERVICE_ID, WORLD as _WORLD

router = APIRouter(tags=["character"])


class AdornSlotResponse(BaseModel):
    color: str
    adorn_name: str | None = None
    adorn_id: str | None = None


class EquipmentSlotResponse(BaseModel):
    slot: str
    name: str
    item_id: str | None = None
    icon_id: str | None = None
    tier: str | None = None
    adorn_slots: list[AdornSlotResponse] = []


class CharacterStats(BaseModel):
    # General
    health_max: int | None = None
    health_regen: int | None = None
    power_max: int | None = None
    power_regen: int | None = None
    run_speed: float | None = None
    status_points: int | None = None
    # Attributes
    str_eff: int | None = None
    sta_eff: int | None = None
    agi_eff: int | None = None
    wis_eff: int | None = None
    int_eff: int | None = None
    # Defense
    armor: int | None = None
    avoidance: int | None = None
    block_chance: float | None = None
    parry: int | None = None
    mit_physical: float | None = None
    mit_elemental: float | None = None
    mit_noxious: float | None = None
    mit_arcane: float | None = None
    # Combat
    potency: float | None = None
    crit_chance: float | None = None
    crit_bonus: float | None = None
    fervor: float | None = None
    dps: float | None = None
    double_attack: float | None = None
    ability_doublecast: float | None = None
    attack_speed: float | None = None
    strikethrough: float | None = None
    accuracy: float | None = None
    ability_mod: float | None = None
    weapon_damage_bonus: float | None = None
    flurry: float | None = None
    lethality: float | None = None
    toughness: float | None = None
    # Casting abilities
    reuse_speed: float | None = None
    casting_speed: float | None = None
    recovery_speed: float | None = None
    # Weapon
    primary_min: int | None = None
    primary_max: int | None = None
    primary_delay: float | None = None
    secondary_min: int | None = None
    secondary_max: int | None = None
    secondary_delay: float | None = None
    ranged_min: int | None = None
    ranged_max: int | None = None
    ranged_delay: float | None = None


def _f(d: dict, *keys: str) -> float | None:
    """Drill into a nested dict and return a float, or None if missing/zero path."""
    cur: object = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    if cur is None:
        return None
    try:
        return float(cur)
    except (TypeError, ValueError):
        return None


def _i(d: dict, *keys: str) -> int | None:
    v = _f(d, *keys)
    return None if v is None else int(v)


def _parse_stats(s: dict) -> CharacterStats:
    health  = s.get("health") or {}
    power   = s.get("power") or {}
    defense = s.get("defense") or {}
    combat  = s.get("combat") or {}
    ability = s.get("ability") or {}
    weapon  = s.get("weapon") or {}

    def attr(name: str) -> int | None:
        return _i(s.get(name) or {}, "effective")

    # Secondary weapons with value -1 mean "not applicable"
    sec_min = _i(weapon, "secondarymindamage")
    sec_max = _i(weapon, "secondarymaxdamage")
    sec_delay = _f(weapon, "secondarydelay")
    if sec_min is not None and sec_min < 0:
        sec_min = sec_max = sec_delay = None

    return CharacterStats(
        health_max        = _i(health, "max"),
        health_regen      = _i(health, "regen"),
        power_max         = _i(power, "max"),
        power_regen       = _i(power, "regen"),
        run_speed         = _f(s, "runspeed"),
        status_points     = _i(s, "personal_status_points"),
        str_eff           = attr("str"),
        sta_eff           = attr("sta"),
        agi_eff           = attr("agi"),
        wis_eff           = attr("wis"),
        int_eff           = attr("int"),
        armor             = _i(defense, "armor"),
        avoidance         = _i(defense, "avoidance"),
        block_chance      = _f(combat, "blockchance"),
        parry             = _i(defense, "parry"),
        mit_physical      = _f(combat, "mitigation_physical"),
        mit_elemental     = _f(combat, "mitigation_elemental"),
        mit_noxious       = _f(combat, "mitigation_noxious"),
        mit_arcane        = _f(combat, "mitigation_arcane"),
        potency           = _f(combat, "basemodifier"),
        crit_chance       = _f(combat, "critchance"),
        crit_bonus        = _f(combat, "critbonus"),
        fervor            = _f(combat, "fervor"),
        dps               = _f(combat, "dps"),
        double_attack     = _f(combat, "doubleattackchance"),
        ability_doublecast= _f(combat, "abilitydoubleattackchance"),
        attack_speed      = _f(combat, "attackspeed"),
        strikethrough     = _f(combat, "strikethrough"),
        accuracy          = _f(combat, "accuracy"),
        ability_mod       = _f(combat, "abilitymod"),
        weapon_damage_bonus=_f(combat, "weapondamagebonus"),
        flurry            = _f(combat, "flurry"),
        lethality         = _f(combat, "lethality"),
        toughness         = _f(combat, "toughness"),
        reuse_speed       = _f(ability, "spelltimereusepct"),
        casting_speed     = _f(ability, "spelltimecastpct"),
        recovery_speed    = _f(ability, "spelltimerecoverypct"),
        primary_min       = _i(weapon, "primarymindamage"),
        primary_max       = _i(weapon, "primarymaxdamage"),
        primary_delay     = _f(weapon, "primarydelay"),
        secondary_min     = sec_min,
        secondary_max     = sec_max,
        secondary_delay   = sec_delay,
        ranged_min        = _i(weapon, "rangedmindamage"),
        ranged_max        = _i(weapon, "rangedmaxdamage"),
        ranged_delay      = _f(weapon, "rangeddelay"),
    )


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
    guild_name: str | None = None
    stats: CharacterStats = CharacterStats()
    equipment: list[EquipmentSlotResponse] = []
    spell_ids: list[int] = []


def _build_char_response(char) -> CharacterResponse:
    """Convert a CharacterOverview into a CharacterResponse (shared by endpoint + guild pre-warming)."""
    return CharacterResponse(
        id         = char.id,
        name       = char.name,
        level      = char.level,
        cls        = char.cls,
        race       = char.race,
        gender     = char.gender,
        deity      = char.deity,
        aa_count   = char.aa_count,
        world      = char.world,
        ts_class   = char.ts_class,
        ts_level   = char.ts_level,
        guild_name = char.guild_name,
        stats      = _parse_stats(char.stats),
        equipment  = [
            EquipmentSlotResponse(
                slot        = s.slot_name,
                name        = s.item_name,
                item_id     = s.item_id,
                icon_id     = s.icon_id,
                tier        = s.tier,
                adorn_slots = [
                    AdornSlotResponse(color=a.color, adorn_name=a.adorn_name, adorn_id=a.adorn_id)
                    for a in s.adorn_slots
                ],
            )
            for s in char.equipment
        ],
        spell_ids = char.spell_ids,
    )


async def prewarm_character_cache() -> None:
    """
    Fetch all approved claimed characters into cache at startup.
    Runs as a background task so it never blocks the server coming up.
    Uses a semaphore to avoid hammering Census with too many parallel requests.
    """
    import aiosqlite
    from web.db import DB_PATH

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT DISTINCT character_name FROM character_claims WHERE status = 'approved'"
            ) as cur:
                names = [row["character_name"] for row in await cur.fetchall()]

        if not names:
            return

        _log.info("[startup] Pre-warming character cache for %d character(s)…", len(names))

        sem = asyncio.Semaphore(3)  # max 3 concurrent Census fetches

        async def _fetch_one(name: str) -> None:
            cache_key = f"{name.lower()}:{_WORLD.lower()}"
            if character_cache.get_stale(cache_key)[0] is not None:
                return  # already warm
            async with sem:
                client = CensusClient(service_id=_SERVICE_ID)
                try:
                    char = await client.get_character(name, _WORLD)
                    if char is not None:
                        character_cache.set(cache_key, _build_char_response(char))
                except Exception as exc:
                    _log.warning("[startup] Pre-warm failed for %s: %s", name, exc)
                finally:
                    await client.close()

        await asyncio.gather(*[_fetch_one(n) for n in names])
        _log.info("[startup] Character cache pre-warm complete.")

    except Exception as exc:
        _log.error("[startup] Character cache pre-warm error: %s", exc)


async def _bg_refresh_character(name: str, cache_key: str) -> None:
    """Background task: silently re-fetch a character and update the cache."""
    try:
        client = CensusClient(service_id=_SERVICE_ID)
        try:
            char = await client.get_character(name, _WORLD)
        finally:
            await client.close()
        if char is not None:
            character_cache.set(cache_key, _build_char_response(char))
    except Exception as exc:
        _log.error("[Cache] Background character refresh failed for %s: %s", name, exc)


@router.get("/character/{name}", response_model=CharacterResponse)
async def get_character(name: str) -> CharacterResponse:
    """
    Fetch a character's overview from the EQ2 Census API.
    Always responds instantly from cache; fires a background refresh when stale.
    """
    if len(name) > 64:
        raise HTTPException(status_code=400, detail="Character name is too long")
    cache_key = f"{name.lower()}:{_WORLD.lower()}"
    cached, is_stale = character_cache.get_stale(cache_key)
    if cached is not None:
        if is_stale:
            asyncio.create_task(_bg_refresh_character(name, cache_key))
        return cached

    # Cache miss — fetch synchronously
    client = CensusClient(service_id=_SERVICE_ID)
    try:
        char = await client.get_character(name, _WORLD)
    finally:
        await client.close()

    if char is None:
        raise HTTPException(status_code=404, detail=f"Character '{name}' not found on {_WORLD}")

    result = _build_char_response(char)
    character_cache.set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Character spell list
# ---------------------------------------------------------------------------


class SpellEntryResponse(BaseModel):
    name:           str
    tier:           str
    level:          int
    spell_type:     str
    icon_id:        int | None = None
    icon_backdrop:  int | None = None


class CharacterSpellsResponse(BaseModel):
    character_name: str
    spells:         list[SpellEntryResponse]
    tier_counts:    dict[str, int]       # all _TIER_ORDER keys present
    tiers_present:  list[str]            # ordered subset that have > 0 spells


@router.get("/character/{name}/spells", response_model=CharacterSpellsResponse)
async def get_character_spells(name: str) -> CharacterSpellsResponse:
    """Return a character's deduplicated spell list resolved from the local spells DB.

    Spell IDs come from the character record that was already fetched (and cached)
    when the character page loaded — no extra Census call needed.
    """
    if not _SPELLS_DB.exists():
        raise HTTPException(status_code=503, detail="Spells database not available")

    # Use the cached character record (populated on first character page load).
    # Fall back to fetching if somehow the cache was cold.
    cache_key = f"{name.lower()}:{_WORLD.lower()}"
    cached, _ = character_cache.get_stale(cache_key)

    if cached is not None:
        char_name = cached.name
        spell_ids = cached.spell_ids
    else:
        client = CensusClient(service_id=_SERVICE_ID)
        try:
            char = await client.get_character(name, _WORLD)
        finally:
            await client.close()
        if char is None:
            raise HTTPException(status_code=404, detail=f"Character '{name}' not found on {_WORLD}")
        result = _build_char_response(char)
        character_cache.set(cache_key, result)
        char_name = result.name
        spell_ids = result.spell_ids

    if not spell_ids:
        return CharacterSpellsResponse(
            character_name=char_name, spells=[], tier_counts={t: 0 for t in _TIER_ORDER}, tiers_present=[]
        )

    # Bulk DB lookup — one query for all IDs
    spell_db: dict[int, dict] = _spell_find_by_ids(spell_ids)

    # Only include spells/arts the character explicitly scribed from a scroll.
    # given_by='spellscroll' is the sole upgradable category — it covers both
    # mage spells and fighter/scout combat arts (confirmed against example data).
    # given_by='class' entries are auto-granted abilities (Invisibility, Call
    # Servant, base combat art ranks etc.) that are permanently fixed in tier.
    blocklist = _load_spell_blocklist()
    rows = [
        r for r in spell_db.values()
        if (r.get("level") or 0) > 0
        and r.get("type") in ("spells", "arts")
        and r.get("given_by") == "spellscroll"
        and _strip_roman(r.get("name") or "").lower() not in blocklist
    ]

    # Deduplicate: per base name+type keep the highest-level entry (highest rank)
    rows = _unique_highest_rows(rows)
    rows.sort(key=lambda r: r.get("level") or 0)

    count = Counter(r.get("tier_name") or "Unknown" for r in rows)

    return CharacterSpellsResponse(
        character_name = char_name,
        spells         = [
            SpellEntryResponse(
                name          = r["name"],
                tier          = r.get("tier_name") or "Unknown",
                level         = r.get("level") or 0,
                spell_type    = r.get("type") or "",
                icon_id       = r.get("icon_id"),
                icon_backdrop = r.get("icon_backdrop"),
            )
            for r in rows
        ],
        tier_counts    = {t: count.get(t, 0) for t in _TIER_ORDER},
        tiers_present  = [t for t in _TIER_ORDER if count.get(t, 0) > 0],
    )


# ---------------------------------------------------------------------------
# Upgrade material summary
# ---------------------------------------------------------------------------

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
    raw_originals: list[str] = []          # lowercased originals that started with "raw "
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
            "item_id":      row[0],
            "display_name": row[1],          # canonical cased name from DB
            "icon_id":      row[2],
            "tier":         tier_raw.title() if tier_raw else None,
            "description":  row[4] or None,
            "item_level":   row[5],
        }

    by_lookup: dict[str, dict] = {}
    with sqlite3.connect(_ITEMS_DB) as conn:
        # ── Pass 1: exact match (displayname_lower IN (...)) ─────────────────
        unique_lookups = list(set(orig_to_lookup.values()))
        placeholders   = ",".join("?" * len(unique_lookups))
        rows = conn.execute(
            f"SELECT id, displayname, icon_id, tier_display, description, item_level "
            f"FROM items WHERE displayname_lower IN ({placeholders})",
            unique_lookups,
        ).fetchall()
        for row in rows:
            key = row[1].lower()   # displayname → lowercase for keying
            if key not in by_lookup:
                by_lookup[key] = _row_to_info(row)

        # ── Pass 2: fuzzy fallback for unmatched "Raw X" names ───────────────
        # For each "raw opaline" whose stripped form "opaline" wasn't found,
        # search for no-value items with max_stack_size=800 whose name contains
        # the keyword ("opaline").  Pick the first match (e.g. "Rough Opaline").
        unmatched_raw = [
            lo for lo in raw_originals
            if orig_to_lookup[lo] not in by_lookup
        ]
        for lo in unmatched_raw:
            keyword = orig_to_lookup[lo]   # e.g. "opaline"
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
    return {
        orig: by_lookup[lookup]
        for orig, lookup in orig_to_lookup.items()
        if lookup in by_lookup
    }


class IngredientResponse(BaseModel):
    name:        str
    quantity:    int
    category:    str            # "primary" | "secondary" | "fuel"
    item_id:     int | None = None
    icon_id:     int | None = None
    tier:        str | None = None
    description: str | None = None
    item_level:  int | None = None


class UpgradeMaterialsResponse(BaseModel):
    spells_needing_upgrade: int   # sub-expert spells found in spell DB
    spells_with_recipe:     int   # of those, how many had an Expert recipe
    ingredients:            list[IngredientResponse]   # aggregated, sorted qty desc within category


@router.get("/character/{name}/upgrade-materials", response_model=UpgradeMaterialsResponse)
async def get_upgrade_materials(name: str) -> UpgradeMaterialsResponse:
    """Return the aggregated crafting materials needed to upgrade all sub-Expert
    spells to Expert tier, using the local recipes DB.
    """
    # Graceful degradation if either DB is missing
    if not _SPELLS_DB.exists():
        raise HTTPException(status_code=503, detail="Spells database not available")
    if not _RECIPES_DB.exists():
        raise HTTPException(status_code=503, detail="Recipes database not available")

    # Reuse cached character record
    cache_key = f"{name.lower()}:{_WORLD.lower()}"
    cached, _ = character_cache.get_stale(cache_key)
    if cached is not None:
        spell_ids = cached.spell_ids
    else:
        client = CensusClient(service_id=_SERVICE_ID)
        try:
            char = await client.get_character(name, _WORLD)
        finally:
            await client.close()
        if char is None:
            raise HTTPException(status_code=404, detail=f"Character '{name}' not found on {_WORLD}")
        result = _build_char_response(char)
        character_cache.set(cache_key, result)
        spell_ids = result.spell_ids

    if not spell_ids:
        return UpgradeMaterialsResponse(spells_needing_upgrade=0, spells_with_recipe=0, ingredients=[])

    # Get spell rows, apply same filter as the spells endpoint
    spell_db: dict[int, dict] = _spell_find_by_ids(spell_ids)
    blocklist = _load_spell_blocklist()
    rows = [
        r for r in spell_db.values()
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
    totals: dict[str, int]   = defaultdict(int)
    cats:   dict[str, str]   = {}

    for recipe in recipes.values():
        if recipe.get("primary_comp"):
            n = recipe["primary_comp"]
            totals[n] += recipe.get("primary_qty") or 1
            cats[n] = "primary"
        for sc in recipe.get("secondary_comps") or []:
            n = sc.get("description") or ""
            if n:
                totals[n] += sc.get("quantity") or 1
                cats[n] = "secondary"
        if recipe.get("fuel_comp"):
            n = recipe["fuel_comp"]
            totals[n] += recipe.get("fuel_qty") or 1
            cats[n] = "fuel"

    # Bulk item DB lookup for icons + tooltip data
    item_data = _lookup_items_by_name(list(totals.keys()))

    # Sort: primary first, then secondary, then fuel; within each group by qty desc
    _cat_order = {"primary": 0, "secondary": 1, "fuel": 2}
    ingredients = sorted(
        [
            IngredientResponse(
                name        = (item_data.get(n.lower(), {}).get("display_name") or n),
                quantity    = q,
                category    = cats[n],
                item_id     = item_data.get(n.lower(), {}).get("item_id"),
                icon_id     = item_data.get(n.lower(), {}).get("icon_id"),
                tier        = item_data.get(n.lower(), {}).get("tier"),
                description = item_data.get(n.lower(), {}).get("description"),
                item_level  = item_data.get(n.lower(), {}).get("item_level"),
            )
            for n, q in totals.items()
        ],
        key=lambda i: (_cat_order.get(i.category, 9), -i.quantity),
    )

    return UpgradeMaterialsResponse(
        spells_needing_upgrade = len(sub_expert),
        spells_with_recipe     = len(recipes),
        ingredients            = ingredients,
    )


class UpgradeRecipesResponse(BaseModel):
    results:               list[_RecipeResult]
    spells_needing_upgrade: int
    spells_with_recipe:     int


@router.get("/character/{name}/upgrade-recipes", response_model=UpgradeRecipesResponse)
async def get_upgrade_recipes(name: str) -> UpgradeRecipesResponse:
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
    cache_key = f"{name.lower()}:{_WORLD.lower()}"
    cached, _ = character_cache.get_stale(cache_key)
    if cached is not None:
        spell_ids = cached.spell_ids
    else:
        client = CensusClient(service_id=_SERVICE_ID)
        try:
            char = await client.get_character(name, _WORLD)
        finally:
            await client.close()
        if char is None:
            raise HTTPException(status_code=404, detail=f"Character '{name}' not found on {_WORLD}")
        result = _build_char_response(char)
        character_cache.set(cache_key, result)
        spell_ids = result.spell_ids

    if not spell_ids:
        return UpgradeRecipesResponse(results=[], spells_needing_upgrade=0, spells_with_recipe=0)

    # Get spell rows, apply same filter as the spells endpoint
    spell_db: dict[int, dict] = _spell_find_by_ids(spell_ids)
    blocklist = _load_spell_blocklist()
    rows = [
        r for r in spell_db.values()
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
            id               = recipe["id"],
            name             = recipe["name"],
            bench            = recipe.get("bench"),
            bench_label      = _recipe_bench_label(recipe.get("bench")),
            craft_tier       = _recipe_fuel_to_craft_tier(recipe.get("fuel_comp")),
            crafted_tier     = recipe.get("crafted_tier"),
            primary_comp     = recipe.get("primary_comp"),
            primary_qty      = recipe.get("primary_qty"),
            secondary_comps  = [
                _RecipeIngredientResponse(
                    description = sc.get("description", ""),
                    quantity    = sc.get("quantity", 1),
                )
                for sc in (recipe.get("secondary_comps") or [])
                if sc.get("description")
            ],
            fuel_comp        = recipe.get("fuel_comp"),
            fuel_qty         = recipe.get("fuel_qty"),
            out_formed_id    = recipe.get("out_formed_id"),
            out_formed_count = recipe.get("out_formed_count"),
            class_label      = None,
        )
        for recipe in recipes.values()
    ]

    return UpgradeRecipesResponse(
        results                = results,
        spells_needing_upgrade = len(sub_expert),
        spells_with_recipe     = len(recipes),
    )
