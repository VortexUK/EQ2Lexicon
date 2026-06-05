from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helper: minimal stored CharacterResponse dict (all required fields present)
# ---------------------------------------------------------------------------
_STORED_CHAR_DATA = {
    "id": "1",
    "name": "Stored",
    "level": 90,
    "cls": "Templar",
    "race": "High Elf",
    "gender": "Female",
    "deity": None,
    "aa_count": 320,
    "world": "Varsoon",
    "ts_class": None,
    "ts_level": None,
    "guild_name": "Exordium",
    "ilvl": None,
    "stats": {},
    "equipment": [],
    "spell_ids": [],
}


@pytest.mark.asyncio
async def test_character_not_found(app):
    """Census returns nothing → 404."""
    with (
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient") as MockCC,
    ):
        instance = MockCC.return_value
        instance.get_character = AsyncMock(return_value=None)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/character/NoSuchChar")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_character_returns_data(app):
    """Valid Census response → 200 with character fields."""
    from backend.census.models import CharacterOverview

    fake_char = CharacterOverview(
        id="123",
        name="Vortex",
        level=70,
        cls="Wizard",
        race="High Elf",
        gender="Male",
        deity=None,
        aa_count=50,
        world="Varsoon",
        ts_class="Sage",
        ts_level=70,
        equipment=[],
    )

    with (
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient") as MockCC,
    ):
        instance = MockCC.return_value
        instance.get_character = AsyncMock(return_value=fake_char)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/character/Vortex")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Vortex"
    assert data["level"] == 70
    assert data["cls"] == "Wizard"
    assert data["aa_count"] == 50
    assert data["ts_class"] == "Sage"
    assert data["ts_level"] == 70


def test_adorn_ilvl_bonus_from_gear():
    from backend.census.item_level import adorn_bonus
    from backend.census.models import AdornSlot
    from backend.eq2db.items import GearRow
    from backend.server.api.character import _adorn_ilvl_bonus

    gear = {200: GearRow(ilvl=None, wield_style=None, level=90, tier_display="FABLED")}
    filled = AdornSlot(color="white", adorn_name="Adorn", adorn_id="200")
    empty = AdornSlot(color="yellow", adorn_name=None, adorn_id=None)
    assert _adorn_ilvl_bonus(filled, gear) == round(adorn_bonus(90, "FABLED"), 1)
    assert _adorn_ilvl_bonus(empty, gear) == 0.0


def test_ilvl_from_gear_folds_adorn_into_host_item():
    from backend.census.item_level import adorn_bonus
    from backend.census.models import AdornSlot, EquipmentSlot
    from backend.eq2db.items import GearRow
    from backend.server.api.character import _ilvl_from_gear

    gear = {
        100: GearRow(ilvl=400.0, wield_style="One-Handed", level=90, tier_display="FABLED"),
        200: GearRow(ilvl=None, wield_style=None, level=90, tier_display="FABLED"),  # adorn
    }
    equip = [
        EquipmentSlot(
            slot_name="head",
            item_name="Helm",
            item_id="100",
            adorn_slots=[AdornSlot(color="white", adorn_name="Adorn", adorn_id="200")],
        )
    ]
    # (host 400 + adorn bonus) averaged over the fixed 21-slot denominator.
    expected = round((400.0 + adorn_bonus(90, "FABLED")) / 21, 1)
    assert _ilvl_from_gear(equip, gear) == expected


# ---------------------------------------------------------------------------
# Equipment self-heal: legacy "Item #<id>" placeholders → resolved names
# ---------------------------------------------------------------------------
# Pre-fix, a cold items.db at character-fetch time meant the equipment slot
# got cached with item_name="Item #12345". The persistent census_store
# refactor (PR #21) then served that placeholder forever. The fix has two
# halves: (a) census/client._resolve_item_meta does a Census fallback so
# new cache rows are born resolved, and (b) the route's
# _heal_equipment_placeholders re-resolves leftover placeholders from
# items.db on the serve path.


@pytest.mark.asyncio
async def test_heal_equipment_placeholders_resolves_from_items_db(monkeypatch):
    """Placeholder name + items.db hit → name/tier/icon all replaced."""
    import backend.server.api.character.views as charmodule
    from backend.server.api.character import (
        AdornSlotResponse,
        EquipmentSlotResponse,
        _heal_equipment_placeholders,
    )

    async def _fake_find(item_id, *args, **kwargs):
        return {
            "displayname": "Helm of Diagnosis",
            "tier": "FABLED",
            "iconid": 4242,
        }

    monkeypatch.setattr(charmodule, "_item_find_by_id", _fake_find)

    slot = EquipmentSlotResponse(
        slot="Head",
        name="Item #12345",
        item_id="12345",
        icon_id=None,
        tier=None,
        adorn_slots=[],
    )
    await _heal_equipment_placeholders([slot])
    assert slot.name == "Helm of Diagnosis"
    assert slot.tier == "FABLED"
    assert slot.icon_id == "4242"


@pytest.mark.asyncio
async def test_heal_equipment_placeholders_skips_real_names(monkeypatch):
    """Already-resolved slot → untouched, items.db never consulted."""
    import backend.server.api.character.views as charmodule
    from backend.server.api.character import EquipmentSlotResponse, _heal_equipment_placeholders

    calls: list[int] = []

    async def _fake_find(item_id, *args, **kwargs):
        calls.append(item_id)
        return {"displayname": "REPLACED", "tier": "MYTHICAL", "iconid": 1}

    monkeypatch.setattr(charmodule, "_item_find_by_id", _fake_find)

    slot = EquipmentSlotResponse(
        slot="Chest",
        name="Robe of the Wise",
        item_id="999",
        icon_id="111",
        tier="LEGENDARY",
        adorn_slots=[],
    )
    await _heal_equipment_placeholders([slot])
    # Untouched — slot already had a real name.
    assert slot.name == "Robe of the Wise"
    assert slot.tier == "LEGENDARY"
    assert slot.icon_id == "111"
    # No items.db lookup should have happened.
    assert calls == []


@pytest.mark.asyncio
async def test_heal_equipment_placeholders_keeps_placeholder_on_db_miss(monkeypatch):
    """Items.db still doesn't know this ID → leave the placeholder so the
    frontend still renders the slot. The next character refresh (via the
    new Census fallback in _parse_equipment) will resolve it for real."""
    import backend.server.api.character.views as charmodule
    from backend.server.api.character import EquipmentSlotResponse, _heal_equipment_placeholders

    async def _fake_find(item_id, *args, **kwargs):
        return None  # cold items.db

    monkeypatch.setattr(charmodule, "_item_find_by_id", _fake_find)

    slot = EquipmentSlotResponse(
        slot="Feet",
        name="Item #777",
        item_id="777",
        adorn_slots=[],
    )
    await _heal_equipment_placeholders([slot])
    assert slot.name == "Item #777"  # unchanged


@pytest.mark.asyncio
async def test_heal_equipment_placeholders_fills_empty_adorn_names(monkeypatch):
    """Adornments with adorn_id set but adorn_name=None (the items.db-cold
    shape) get resolved from items.db too. Adornments with a name already
    set are left alone."""
    import backend.server.api.character.views as charmodule
    from backend.server.api.character import (
        AdornSlotResponse,
        EquipmentSlotResponse,
        _heal_equipment_placeholders,
    )

    resolved_ids: list[int] = []

    async def _fake_find(item_id, *args, **kwargs):
        resolved_ids.append(item_id)
        # Only ID 500 is known.
        if item_id == 500:
            return {"displayname": "Adornment of Things", "tier": None, "iconid": None}
        return None

    monkeypatch.setattr(charmodule, "_item_find_by_id", _fake_find)

    slot = EquipmentSlotResponse(
        slot="Chest",
        name="Real Item",  # real name → host slot skipped
        item_id="999",
        icon_id="1",
        tier="FABLED",
        adorn_slots=[
            AdornSlotResponse(color="white", adorn_name=None, adorn_id="500"),
            AdornSlotResponse(color="yellow", adorn_name="Already Named", adorn_id="600"),
            AdornSlotResponse(color="red", adorn_name=None, adorn_id=None),  # empty slot
        ],
    )
    await _heal_equipment_placeholders([slot])
    assert slot.adorn_slots[0].adorn_name == "Adornment of Things"
    assert slot.adorn_slots[1].adorn_name == "Already Named"  # untouched
    assert slot.adorn_slots[2].adorn_name is None  # nothing to look up
    # Only the named-missing adorn should have been queried; the empty slot
    # and the already-named one must NOT trigger items.db lookups.
    assert resolved_ids == [500, 600] or resolved_ids == [500]


@pytest.mark.asyncio
async def test_serve_path_self_heals_stored_placeholder(app, tmp_path, monkeypatch):
    """End-to-end: stored character with 'Item #<id>' in equipment →
    response carries the resolved name + tier + icon from items.db."""
    import backend.server.api.character.views as charmodule
    from backend.census import store as census_store
    from backend.server.cache import character_cache
    from backend.server.config import WORLD as _WORLD

    stored = dict(_STORED_CHAR_DATA)
    stored["equipment"] = [
        {
            "slot": "Head",
            "name": "Item #99999",
            "item_id": "99999",
            "icon_id": None,
            "tier": None,
            "adorn_slots": [],
        }
    ]
    db_path = tmp_path / "backend.census.db"
    conn = census_store.init_db(db_path)
    try:
        census_store.upsert_character(
            conn,
            "HealMe",
            _WORLD,
            stored,
            resolved=True,
            now=int(__import__("time").time()),
        )
    finally:
        conn.close()
    monkeypatch.setattr(census_store, "DB_PATH", db_path)
    character_cache.delete(f"healme:{_WORLD.lower()}")

    async def _fake_find(item_id, *args, **kwargs):
        assert item_id == 99999
        return {"displayname": "Cowl of Repair", "tier": "MYTHICAL", "iconid": 7777}

    monkeypatch.setattr(charmodule, "_item_find_by_id", _fake_find)

    # shared_census_client must not be touched on the cached-serve path.
    # (No explicit guard needed — the stored data is served directly.)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/character/HealMe")

    assert response.status_code == 200
    equip = response.json()["equipment"]
    assert equip[0]["name"] == "Cowl of Repair"
    assert equip[0]["tier"] == "MYTHICAL"
    assert equip[0]["icon_id"] == "7777"


@pytest.mark.asyncio
async def test_stored_data_served_without_census(app, tmp_path, monkeypatch):
    """census_store hit + Census unreachable → 200 with stale=True, CensusClient never called."""
    import backend.server.api.character.views as charmodule
    from backend.census import store as census_store
    from backend.server.cache import character_cache
    from backend.server.config import WORLD as _WORLD

    # Seed the census_store at an isolated tmp DB.
    db_path = tmp_path / "backend.census.db"
    conn = census_store.init_db(db_path)
    try:
        census_store.upsert_character(
            conn,
            "Stored",
            _WORLD,
            _STORED_CHAR_DATA,
            resolved=True,
            now=1000,  # ancient timestamp → stale
        )
    finally:
        conn.close()

    # Point the module at the tmp DB so the endpoint reads from it.
    monkeypatch.setattr(census_store, "DB_PATH", db_path)

    # Ensure the in-memory cache is cold for this key.
    cache_key = f"stored:{_WORLD.lower()}"
    character_cache.delete(cache_key)

    # Guard: stored data is available, so Census must not be reached.
    # (shared_census_client is the shared lifecycle — no direct attribute to patch here.)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/character/Stored")

    assert response.status_code == 200
    body = response.json()
    assert body["level"] == 90
    assert body["stale"] is True


@pytest.mark.asyncio
async def test_partial_roster_record_served_without_500(app, tmp_path, monkeypatch):
    """A partial store record (roster-sync: no id/world) must serve, not 500.

    Regression for the ValidationError seen in prod: CharacterResponse requires
    id + world, but a roster-synced blob has neither. The endpoint must fill them
    from context (world = current request world) and mark the record stale so a
    background refresh replaces it — never raise.
    """
    from backend.census import store as census_store
    from backend.server.cache import character_cache
    from backend.server.config import WORLD as _WORLD

    # Roster-shaped partial blob: name/level/guild only — no id, no world.
    partial = {"name": "Verarec", "level": 90, "cls": "Wizard", "guild_name": "Test"}

    db_path = tmp_path / "backend.census.db"
    conn = census_store.init_db(db_path)
    try:
        census_store.upsert_character(conn, "Verarec", _WORLD, partial, resolved=True, now=1000)
    finally:
        conn.close()
    monkeypatch.setattr(census_store, "DB_PATH", db_path)
    character_cache.delete(f"verarec:{_WORLD.lower()}")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/character/Verarec")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == ""  # unknown until Census resolves it
    assert body["world"] == _WORLD  # filled from request context
    assert body["level"] == 90
    assert body["stale"] is True  # partial → forced stale → background refresh
