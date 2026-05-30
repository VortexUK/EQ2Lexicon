"""GET /character/{name} + the shared CharacterResponse model + equipment helpers.

Carved out of the original 933-line web/routes/character.py.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from fastapi import HTTPException, Request
from pydantic import BaseModel

from census.db import GearRow, gear_for_ids
from census.db import find_by_id as _item_find_by_id
from census.item_level import adorn_bonus, character_ilvl
from web.cache import character_cache
from web.constants import CHARACTER_STALE_S
from web.lib.cache_keys import char_cache_key
from web.lib.census_lifecycle import shared_census_client
from web.lib.validation import validate_character_name
from web.limiter import limiter
from web.routes.character import router  # the package-level router
from web.server_context import current_world

_log = logging.getLogger(__name__)


class AdornSlotResponse(BaseModel):
    color: str
    adorn_name: str | None = None
    adorn_id: str | None = None
    ilvl_bonus: float = 0.0  # how much this adorn adds to the host item's ilvl


class EquipmentSlotResponse(BaseModel):
    slot: str
    name: str
    item_id: str | None = None
    icon_id: str | None = None
    tier: str | None = None
    adorn_slots: list[AdornSlotResponse] = []


# ---------------------------------------------------------------------------
# Equipment self-heal
# ---------------------------------------------------------------------------
# When a character was fetched while items.db was cold for some item ID, the
# census client's _parse_equipment fell back to the literal "Item #<id>"
# placeholder and that placeholder got baked into the cached character row
# inside census_store (PR #21). The fix in census.client._resolve_item_meta
# prevents NEW cache rows from being born stale, but existing rows hold the
# placeholder until they next refresh. Most of those items have since been
# resolved into items.db (every tooltip click upserts), so a fast items.db
# lookup at serve time will recover the correct display values without
# needing a Census round-trip. Items still missing from items.db stay as
# the placeholder; the next character refresh (≥ STALE_S seconds later)
# will resolve them via the new Census fallback path.

_ITEM_PLACEHOLDER_RE = re.compile(r"^Item #(-?\d+)$")


async def _heal_equipment_placeholders(slots: list[EquipmentSlotResponse]) -> None:
    """Replace any ``Item #<id>`` placeholder names + missing icons in-place,
    using items.db as the only source (no Census call — keeps the serve
    path fast). Adornment names get the same treatment. No-op for slots
    that already carry a real name."""
    for slot in slots:
        m = _ITEM_PLACEHOLDER_RE.match(slot.name or "")
        if m:
            try:
                item_id = int(m.group(1))
            except ValueError:
                continue
            row = await _item_find_by_id(item_id)
            if row:
                resolved = row.get("displayname")
                if resolved:
                    slot.name = resolved
                    slot.tier = str(row.get("tier") or "") or None
                    if row.get("iconid"):
                        slot.icon_id = str(row["iconid"])

        # Same lookup for adornments — they suffered the same items.db-cold
        # bug, just stored as None instead of a placeholder string. Re-
        # resolving anything missing is cheap and helps the gear tooltip
        # render adornment names where it currently shows nothing.
        for adorn in slot.adorn_slots:
            if adorn.adorn_name or not adorn.adorn_id:
                continue
            try:
                adorn_id = int(adorn.adorn_id)
            except ValueError:
                continue
            row = await _item_find_by_id(adorn_id)
            if row:
                name = row.get("displayname")
                if name:
                    adorn.adorn_name = name


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
        return float(cur)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _i(d: dict, *keys: str) -> int | None:
    v = _f(d, *keys)
    return None if v is None else int(v)


def _parse_stats(s: dict) -> CharacterStats:
    health = s.get("health") or {}
    power = s.get("power") or {}
    defense = s.get("defense") or {}
    combat = s.get("combat") or {}
    ability = s.get("ability") or {}
    weapon = s.get("weapon") or {}

    def attr(name: str) -> int | None:
        return _i(s.get(name) or {}, "effective")

    # Secondary weapons with value -1 mean "not applicable"
    sec_min = _i(weapon, "secondarymindamage")
    sec_max = _i(weapon, "secondarymaxdamage")
    sec_delay = _f(weapon, "secondarydelay")
    if sec_min is not None and sec_min < 0:
        sec_min = sec_max = sec_delay = None

    return CharacterStats(
        health_max=_i(health, "max"),
        health_regen=_i(health, "regen"),
        power_max=_i(power, "max"),
        power_regen=_i(power, "regen"),
        run_speed=_f(s, "runspeed"),
        status_points=_i(s, "personal_status_points"),
        str_eff=attr("str"),
        sta_eff=attr("sta"),
        agi_eff=attr("agi"),
        wis_eff=attr("wis"),
        int_eff=attr("int"),
        armor=_i(defense, "armor"),
        avoidance=_i(defense, "avoidance"),
        block_chance=_f(combat, "blockchance"),
        parry=_i(defense, "parry"),
        mit_physical=_f(combat, "mitigation_physical"),
        mit_elemental=_f(combat, "mitigation_elemental"),
        mit_noxious=_f(combat, "mitigation_noxious"),
        mit_arcane=_f(combat, "mitigation_arcane"),
        potency=_f(combat, "basemodifier"),
        crit_chance=_f(combat, "critchance"),
        crit_bonus=_f(combat, "critbonus"),
        fervor=_f(combat, "fervor"),
        dps=_f(combat, "dps"),
        double_attack=_f(combat, "doubleattackchance"),
        ability_doublecast=_f(combat, "abilitydoubleattackchance"),
        attack_speed=_f(combat, "attackspeed"),
        strikethrough=_f(combat, "strikethrough"),
        accuracy=_f(combat, "accuracy"),
        ability_mod=_f(combat, "abilitymod"),
        weapon_damage_bonus=_f(combat, "weapondamagebonus"),
        flurry=_f(combat, "flurry"),
        lethality=_f(combat, "lethality"),
        toughness=_f(combat, "toughness"),
        reuse_speed=_f(ability, "spelltimereusepct"),
        casting_speed=_f(ability, "spelltimecastpct"),
        recovery_speed=_f(ability, "spelltimerecoverypct"),
        primary_min=_i(weapon, "primarymindamage"),
        primary_max=_i(weapon, "primarymaxdamage"),
        primary_delay=_f(weapon, "primarydelay"),
        secondary_min=sec_min,
        secondary_max=sec_max,
        secondary_delay=sec_delay,
        ranged_min=_i(weapon, "rangedmindamage"),
        ranged_max=_i(weapon, "rangedmaxdamage"),
        ranged_delay=_f(weapon, "rangeddelay"),
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
    ilvl: float | None = None  # average gear ilvl; None if no gear / items.db absent
    stats: CharacterStats = CharacterStats()
    equipment: list[EquipmentSlotResponse] = []
    spell_ids: list[int] = []
    fetched_at: int | None = None  # unix s of last resolved data (freshness)
    stale: bool = False  # served from store older than the staleness window


def _ilvl_from_gear(equipment, gear: dict[int, GearRow]) -> float | None:
    """Compute a character's average gear ilvl from already-fetched gear data.

    ``gear`` maps item_id → GearRow (covers both worn items and their socketed
    adorns). Each worn item's ilvl gets a small bonus per socketed adorn (from
    the adorn's level + tier). Two-handed detection iterates *this* character's
    equipment so a shared (guild-wide) gear map is safe. See
    census.item_level.character_ilvl for the denominator rules."""
    item_ilvls: list[float | None] = []
    two_handed = False
    for s in equipment:
        if not (s.item_id and str(s.item_id).isdigit()):
            continue
        row = gear.get(int(s.item_id))
        if row and row.wield_style == "Two-Handed":
            two_handed = True
        ilvl = row.ilvl if row else None
        if ilvl is not None:
            for a in s.adorn_slots:
                if a.adorn_id and str(a.adorn_id).isdigit():
                    ar = gear.get(int(a.adorn_id))
                    if ar:
                        ilvl += adorn_bonus(ar.level, ar.tier_display)
        item_ilvls.append(ilvl)
    return character_ilvl(item_ilvls, two_handed=two_handed)


def _equipment_lookup_ids(equipment) -> list[int]:
    """All numeric item ids on a character: worn items + their socketed adorns."""
    ids: list[int] = []
    for s in equipment:
        if s.item_id and str(s.item_id).isdigit():
            ids.append(int(s.item_id))
        for a in s.adorn_slots:
            if a.adorn_id and str(a.adorn_id).isdigit():
                ids.append(int(a.adorn_id))
    return ids


def _adorn_ilvl_bonus(adorn, gear: dict[int, GearRow]) -> float:
    """The ilvl bonus a socketed adorn contributes, rounded for display."""
    if not (adorn.adorn_id and str(adorn.adorn_id).isdigit()):
        return 0.0
    row = gear.get(int(adorn.adorn_id))
    return round(adorn_bonus(row.level, row.tier_display), 1) if row else 0.0


def _build_char_response(char) -> CharacterResponse:
    """Convert a CharacterOverview into a CharacterResponse (shared by endpoint + guild pre-warming)."""
    # One items.db query covers worn items + adorns; reused for the character
    # ilvl and the per-adorn bonus surfaced on each equipment slot.
    gear = gear_for_ids(_equipment_lookup_ids(char.equipment))
    return CharacterResponse(
        ilvl=_ilvl_from_gear(char.equipment, gear),
        id=char.id,
        name=char.name,
        level=char.level,
        cls=char.cls,
        race=char.race,
        gender=char.gender,
        deity=char.deity,
        aa_count=char.aa_count,
        world=char.world,
        ts_class=char.ts_class,
        ts_level=char.ts_level,
        guild_name=char.guild_name,
        stats=_parse_stats(char.stats),
        equipment=[
            EquipmentSlotResponse(
                slot=s.slot_name,
                name=s.item_name,
                item_id=s.item_id,
                icon_id=s.icon_id,
                tier=s.tier,
                adorn_slots=[
                    AdornSlotResponse(
                        color=a.color,
                        adorn_name=a.adorn_name,
                        adorn_id=a.adorn_id,
                        ilvl_bonus=_adorn_ilvl_bonus(a, gear),
                    )
                    for a in s.adorn_slots
                ],
            )
            for s in char.equipment
        ],
        spell_ids=char.spell_ids,
    )


async def _prewarm_for_world(world: str, sem: asyncio.Semaphore) -> None:
    """Pre-warm the character cache for all approved claims in one world."""
    import aiosqlite

    from web.db import DB_PATH

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT DISTINCT character_name FROM character_claims WHERE status = 'approved' AND world = ?",
                (world,),
            ) as cur:
                names = [row["character_name"] for row in await cur.fetchall()]

        if not names:
            return

        _log.info("[startup] Pre-warming character cache for %d character(s) on %s...", len(names), world)

        failures: list[tuple[str, Exception]] = []

        async def _fetch_one(name: str) -> None:
            cache_key = char_cache_key(name, world)
            if character_cache.get_stale(cache_key)[0] is not None:
                return  # already warm
            async with sem:
                try:
                    async with shared_census_client() as client:
                        char = await client.get_character(name, world)
                    if char is not None:
                        character_cache.set(cache_key, _build_char_response(char))
                except Exception as exc:
                    failures.append((name, exc))

        await asyncio.gather(*[_fetch_one(n) for n in names])
        if failures:
            _log.warning(
                "[startup] Pre-warm failed for %d character(s) on %s (first: %s — %s)",
                len(failures),
                world,
                failures[0][0],
                failures[0][1],
            )

    except Exception as exc:
        _log.warning("[startup] Character cache pre-warm error for %s: %s", world, exc)


async def prewarm_character_cache() -> None:
    """
    Fetch all approved claimed characters into cache at startup, for every
    registered server. Runs as a background task so it never blocks the server
    coming up. Uses a shared semaphore to avoid hammering Census with too many
    parallel requests across all servers combined.

    BE-116: iterates the server registry so Wuoshi (and any future server)
    pre-warms at boot, not just the default server.
    """
    from web.db.servers import list_servers_sync
    from web.lib.executor import run_sync

    try:
        servers = await run_sync(list_servers_sync)
    except Exception as exc:
        _log.warning("[startup] Could not load server registry for pre-warm: %s", exc)
        return

    sem = asyncio.Semaphore(3)  # max 3 concurrent Census fetches across ALL servers
    await asyncio.gather(*[_prewarm_for_world(srv["world"], sem) for srv in servers])
    _log.info("[startup] Character cache pre-warm complete (%d server(s)).", len(servers))


@router.get("/character/{name}", response_model=CharacterResponse)
@limiter.limit("30/minute")
async def get_character(request: Request, name: str) -> CharacterResponse:
    """Serve last-known data instantly; refresh from Census only in the
    background. Never blocks on / fails because of Census."""
    sanitised = validate_character_name(name)
    if sanitised is None:
        raise HTTPException(status_code=400, detail="Character name is invalid (must be 1-15 letters).")
    name = sanitised
    cache_key = char_cache_key(name, current_world())
    now = int(time.time())
    STALE_S = CHARACTER_STALE_S

    # 1) Hot in-memory copy.
    cached, is_stale = character_cache.get_stale(cache_key)
    if cached is not None and not is_stale:
        return cached

    # 2) Durable store.
    from census import census_store

    conn = census_store.init_db(census_store.DB_PATH)
    try:
        rec = census_store.get_character(conn, name, current_world())
    finally:
        conn.close()
    if rec is not None:
        from web.census_refresh import request_character_refresh

        stale = (now - rec["last_resolved_at"]) > STALE_S
        if stale:
            request_character_refresh(name)  # throttled/health-gated background refresh
        resp = CharacterResponse(**{**rec["data"], "fetched_at": rec["last_resolved_at"], "stale": stale})
        # Self-heal any "Item #<id>" placeholders left over from a cold
        # items.db at fetch time (see _heal_equipment_placeholders above
        # for the full backstory). items.db-only lookup so this stays
        # fast on the hot serve path; the new client-side Census fallback
        # in census/client.py handles whatever items.db still doesn't
        # know on the next refresh.
        await _heal_equipment_placeholders(resp.equipment)
        # Write the healed response back to the durable store so the
        # next request (and every other process / worker) sees the
        # resolved names immediately, without paying the items.db
        # lookups again. Preserves last_resolved_at so the staleness
        # window doesn't reset — this is a name-fixup, not a refresh.
        # Best-effort; the user already has a correct response in hand
        # so a write failure doesn't degrade the visible behaviour.
        try:
            conn2 = census_store.init_db(census_store.DB_PATH)
            try:
                census_store.upsert_character(
                    conn2,
                    name,
                    current_world(),
                    resp.model_dump(),
                    resolved=True,
                    now=rec["last_resolved_at"],
                )
            finally:
                conn2.close()
        except Exception as exc:
            _log.debug("[character] self-heal cache write skipped for %s: %s", name, exc)
        character_cache.set(cache_key, resp)
        return resp

    # 3) Never seen. Try one live fetch; if Census is down, return a clean
    #    503 (frontend renders the "not cached yet" message) rather than a 500.
    from web import census_health

    if census_health.is_down():
        _log.debug("[character] Skipping live fetch — census_health=down (name=%s)", name)
        raise HTTPException(
            status_code=503,
            detail=f"'{name}' not cached yet and Census is unavailable. Try again shortly.",
        )
    try:
        async with shared_census_client() as client:
            char = await client.get_character(name, current_world())
    except Exception:
        raise HTTPException(
            status_code=503,
            detail=f"'{name}' not cached yet and Census is unavailable. Try again shortly.",
        )
    if char is None:
        raise HTTPException(status_code=404, detail=f"Character '{name}' not found on {current_world()}")
    resp = _build_char_response(char)
    data = resp.model_dump()
    conn = census_store.init_db(census_store.DB_PATH)
    try:
        census_store.upsert_character(conn, name, current_world(), data, resolved=True, now=now)
    finally:
        conn.close()
    resp.fetched_at = now
    character_cache.set(cache_key, resp)
    return resp
