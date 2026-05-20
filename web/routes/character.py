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
    stats: CharacterStats = CharacterStats()
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
        stats     = _parse_stats(char.stats),
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
