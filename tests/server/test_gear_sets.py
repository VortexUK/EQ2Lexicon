"""Tests for the gear-sets endpoint's census_store integration.

Mirrors tests/server/test_aa_census_store.py — the gear-sets read path is
the same store-first SWR shape:
  - Cold cache: Census is called + data is persisted to census_store.
  - Warm store: census_store is served without calling Census.
  - Stale store record: served immediately, background refresh spawned.
  - Census down + nothing cached → 503.
  - Character with no saved sets → 200 with an empty list.
  - Unknown character → 404.
  - init_db on a pre-existing census_store DB (without character_gear_sets
    table) doesn't crash — simulates an upgrade scenario.
"""

from __future__ import annotations

import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.census import store as cs
from backend.census.models import AdornSlot, EquipmentSlot, GearSet

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_gear_sets() -> list[GearSet]:
    """Two small but realistic saved sets."""
    return [
        GearSet(
            name="DPS",
            equipment=[
                EquipmentSlot(
                    slot_name="Head",
                    item_name="Cowl of Fervor",
                    item_id="1001",
                    icon_id="42",
                    tier="FABLED",
                    adorn_slots=[AdornSlot(color="White", adorn_name="Mender's Seal", adorn_id="2001")],
                ),
                EquipmentSlot(slot_name="Chest", item_name="Robe of Woe", item_id="1002"),
            ],
        ),
        GearSet(name="Tank", equipment=[EquipmentSlot(slot_name="Head", item_name="Helm of Stone", item_id="1003")]),
    ]


def _patch_char_id(char_id: str = "12345"):
    """Patch the module's character_cache so _character resolves without a
    full CharacterOverview round-trip."""
    mock_cache = MagicMock()
    mock_cache.get_stale.return_value = (MagicMock(id=char_id, equipment=[]), False)
    return patch("backend.server.api.character.gear_sets.character_cache", mock_cache)


# ---------------------------------------------------------------------------
# Cold-cache test: Census called, data persisted to census_store.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cold_cache_calls_census_and_persists(app, tmp_path):
    """On first fetch (no cache, no store), Census is queried and the result
    is written to census_store."""
    db_path = tmp_path / "backend.census.db"

    with (
        patch("backend.server.api.character.gear_sets.census_store.path", db_path),
        _patch_char_id("777"),
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient") as MockCC,
    ):
        mock_client = MagicMock()
        mock_client.get_gear_sets = AsyncMock(return_value=_make_gear_sets())
        MockCC.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/character/Coldchar/gear-sets")

    assert resp.status_code == 200
    data = resp.json()
    assert data["character_name"] == "Coldchar"
    assert [s["name"] for s in data["sets"]] == ["DPS", "Tank"]
    assert data["sets"][0]["equipment"][0]["name"] == "Cowl of Fervor"
    assert data["sets"][0]["equipment"][0]["adorn_slots"][0]["adorn_name"] == "Mender's Seal"
    mock_client.get_gear_sets.assert_awaited_once_with("777")

    # Verify persisted to the store.
    conn = cs.CensusStore(db_path).init_db()
    try:
        rec = cs.CensusStore.get_character_gear_sets(conn, "Coldchar", "Varsoon")
        assert rec is not None
        assert [s["name"] for s in rec["data"]["sets"]] == ["DPS", "Tank"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Warm-store test: census_store served without calling Census.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warm_store_skips_census(app, tmp_path):
    """When census_store has a fresh record, Census is NOT called."""
    db_path = tmp_path / "backend.census.db"
    conn = cs.CensusStore(db_path).init_db()
    stored_data = {"character_name": "Stored", "sets": [{"name": "Old set", "ilvl": None, "equipment": []}]}
    cs.CensusStore.upsert_character_gear_sets(conn, "Stored", "Varsoon", stored_data, now=int(time.time()))
    conn.close()

    with (
        patch("backend.server.api.character.gear_sets.census_store.path", db_path),
        patch("backend.server.api.character.gear_sets.gear_sets_cache") as mock_cache,
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient") as MockCC,
    ):
        mock_cache.get_stale.return_value = (None, False)
        mock_cache.set = MagicMock()

        mock_client = MagicMock()
        mock_client.get_gear_sets = AsyncMock(return_value=None)
        MockCC.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/character/Stored/gear-sets")

    assert resp.status_code == 200
    assert resp.json()["sets"][0]["name"] == "Old set"
    mock_client.get_gear_sets.assert_not_called()


# ---------------------------------------------------------------------------
# Stale-store test: served immediately, background refresh spawned.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_store_returns_data_and_spawns_refresh(app, tmp_path):
    """A store record older than CHARACTER_STALE_S is served immediately but
    triggers a background refresh task."""
    from backend.server.constants import CHARACTER_STALE_S

    db_path = tmp_path / "backend.census.db"
    old_ts = int(time.time()) - CHARACTER_STALE_S - 60
    conn = cs.CensusStore(db_path).init_db()
    stored_data = {"character_name": "Stalechar", "sets": []}
    cs.CensusStore.upsert_character_gear_sets(conn, "Stalechar", "Varsoon", stored_data, now=old_ts)
    conn.close()

    tasks_created: list = []

    def _fake_create_task(coro):
        tasks_created.append(coro)
        coro.close()  # never run it — just silence the un-awaited warning
        return MagicMock()

    with (
        patch("backend.server.api.character.gear_sets.census_store.path", db_path),
        patch("backend.server.api.character.gear_sets.gear_sets_cache") as mock_cache,
        patch("backend.server.api.character.gear_sets.asyncio.create_task", side_effect=_fake_create_task),
        patch("backend.server.core.census_lifecycle._clients", {}),
    ):
        mock_cache.get_stale.return_value = (None, False)
        mock_cache.set = MagicMock()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/character/Stalechar/gear-sets")

    assert resp.status_code == 200
    assert resp.json()["character_name"] == "Stalechar"
    assert len(tasks_created) == 1


# ---------------------------------------------------------------------------
# Census down + never seen → 503.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_census_down_and_no_store_returns_503(app, tmp_path):
    db_path = tmp_path / "backend.census.db"

    with (
        patch("backend.server.api.character.gear_sets.census_store.path", db_path),
        patch("backend.server.census_health.is_down", return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/character/Nevercached/gear-sets")

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# No saved sets → 200 with empty list (valid, cacheable answer).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_character_with_no_sets_returns_empty_list(app, tmp_path):
    db_path = tmp_path / "backend.census.db"

    with (
        patch("backend.server.api.character.gear_sets.census_store.path", db_path),
        _patch_char_id(),
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient") as MockCC,
    ):
        mock_client = MagicMock()
        mock_client.get_gear_sets = AsyncMock(return_value=[])
        MockCC.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/character/Setless/gear-sets")

    assert resp.status_code == 200
    assert resp.json() == {"character_name": "Setless", "sets": []}

    # The empty answer is persisted too — repeat visitors skip Census.
    conn = cs.CensusStore(db_path).init_db()
    try:
        assert cs.CensusStore.get_character_gear_sets(conn, "Setless", "Varsoon") is not None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Unknown character → 404 from the character-id resolution step.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_character_returns_404(app, tmp_path):
    db_path = tmp_path / "backend.census.db"
    mock_char_cache = MagicMock()
    mock_char_cache.get_stale.return_value = (None, False)  # no cached character

    with (
        patch("backend.server.api.character.gear_sets.census_store.path", db_path),
        patch("backend.server.api.character.gear_sets.character_cache", mock_char_cache),
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient") as MockCC,
    ):
        mock_client = MagicMock()
        mock_client.get_character = AsyncMock(return_value=None)
        MockCC.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/character/Ghostchar/gear-sets")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Migration safety: init_db on a pre-existing DB without character_gear_sets.
# ---------------------------------------------------------------------------


def test_init_db_on_old_schema_adds_gear_sets_table(tmp_path):
    """init_db on an existing census.db without character_gear_sets should not
    crash and should create the new table (simulates an upgrade scenario).

    Memory [test-migrations-against-old-db-shape].
    """
    db_path = tmp_path / "backend.census.db"
    conn = sqlite3.connect(db_path)
    conn.execute(cs._SQL["schema_characters"])
    conn.execute(cs._SQL["schema_guilds"])
    conn.execute(cs._SQL["schema_character_aas"])
    conn.commit()
    conn.close()

    conn = cs.CensusStore(db_path).init_db()
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "character_gear_sets" in tables
    finally:
        conn.close()
