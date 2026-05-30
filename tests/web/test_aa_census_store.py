"""Tests for the AA endpoint's census_store integration (Task 2c.6).

Verifies:
  - Cold cache: Census is called + data is persisted to census_store.
  - Warm store: census_store is served without calling Census.
  - Stale store record: census_store is served immediately, background
    refresh is spawned.
  - init_db on a pre-existing census_store DB (without character_aas table)
    doesn't crash — simulates an upgrade scenario.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from census import census_store as cs
from web.lib.cache_keys import aa_cache_key

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_census_aas(name: str = "Testchar") -> MagicMock:
    """Return a minimal mock CharacterAAs object."""
    node = MagicMock()
    node.tree_id = 1
    node.node_id = 101
    node.tier = 5

    profile_node = MagicMock()
    profile_node.tree_id = 1
    profile_node.node_id = 101
    profile_node.tier = 3

    profile = MagicMock()
    profile.name = "Profile1"
    profile.aa_list = [profile_node]

    aas = MagicMock()
    aas.character_name = name
    aas.aa_list = [node]
    aas.profiles = [profile]
    return aas


# ---------------------------------------------------------------------------
# Cold-cache test: Census called, data persisted to census_store.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cold_cache_calls_census_and_persists(app, tmp_path):
    """On first fetch (no cache, no store), Census is queried and the result
    is written to census_store."""
    db_path = tmp_path / "census.db"
    fake_aas = _make_census_aas("Coldchar")

    with (
        patch("web.routes.aa.census_store.DB_PATH", db_path),
        patch("web.lib.census_lifecycle._clients", {}),
        patch("web.lib.census_lifecycle.CensusClient") as MockCC,
    ):
        mock_client = MagicMock()
        mock_client.get_character_aas = AsyncMock(return_value=fake_aas)
        MockCC.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/character/Coldchar/aas")

    assert resp.status_code == 200
    data = resp.json()
    assert data["character_name"] == "Coldchar"
    assert data["total_spent"] == 5  # one node, tier=5

    # Verify persisted to the store.
    conn = cs.init_db(db_path)
    try:
        rec = cs.get_character_aas(conn, "Coldchar", "Varsoon")
        assert rec is not None
        assert rec["data"]["character_name"] == "Coldchar"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Warm-store test: census_store served without calling Census.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warm_store_skips_census(app, tmp_path):
    """When census_store has a fresh record, Census is NOT called."""
    db_path = tmp_path / "census.db"
    # Pre-seed the store with a recent timestamp.
    conn = cs.init_db(db_path)
    stored_data = {
        "character_name": "Stored",
        "total_spent": 10,
        "trees": [],
        "profiles": [],
    }
    cs.upsert_character_aas(conn, "Stored", "Varsoon", stored_data, now=int(time.time()))
    conn.close()

    with (
        patch("web.routes.aa.census_store.DB_PATH", db_path),
        patch("web.routes.aa.aa_cache") as mock_cache,
        patch("web.lib.census_lifecycle._clients", {}),
        patch("web.lib.census_lifecycle.CensusClient") as MockCC,
    ):
        # Cache miss so we fall through to the store.
        mock_cache.get_stale.return_value = (None, False)
        mock_cache.set = MagicMock()

        mock_client = MagicMock()
        mock_client.get_character_aas = AsyncMock(return_value=None)
        MockCC.return_value = mock_client

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/character/Stored/aas")

    assert resp.status_code == 200
    data = resp.json()
    assert data["character_name"] == "Stored"
    # Census should NOT have been called.
    mock_client.get_character_aas.assert_not_called()


# ---------------------------------------------------------------------------
# Stale-store test: served immediately, background refresh spawned.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_store_returns_data_and_spawns_refresh(app, tmp_path):
    """A store record older than CHARACTER_STALE_S is served immediately but
    triggers a background refresh task."""
    from web.constants import CHARACTER_STALE_S

    db_path = tmp_path / "census.db"
    old_ts = int(time.time()) - CHARACTER_STALE_S - 60  # definitely stale
    conn = cs.init_db(db_path)
    stored_data = {
        "character_name": "Stalechar",
        "total_spent": 7,
        "trees": [],
        "profiles": [],
    }
    cs.upsert_character_aas(conn, "Stalechar", "Varsoon", stored_data, now=old_ts)
    conn.close()

    tasks_created: list = []

    def _fake_create_task(coro):
        tasks_created.append(coro)
        # Don't actually run it.
        return MagicMock()

    with (
        patch("web.routes.aa.census_store.DB_PATH", db_path),
        patch("web.routes.aa.aa_cache") as mock_cache,
        patch("web.routes.aa.asyncio.create_task", side_effect=_fake_create_task),
        patch("web.lib.census_lifecycle._clients", {}),
    ):
        mock_cache.get_stale.return_value = (None, False)
        mock_cache.set = MagicMock()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/character/Stalechar/aas")

    assert resp.status_code == 200
    data = resp.json()
    assert data["character_name"] == "Stalechar"
    # A background task should have been spawned.
    assert len(tasks_created) == 1


# ---------------------------------------------------------------------------
# Migration safety: init_db on a pre-existing DB without character_aas table.
# ---------------------------------------------------------------------------


def test_init_db_on_old_schema_adds_character_aas_table(tmp_path):
    """init_db on an existing census.db without character_aas should not crash
    and should create the new table (simulates an upgrade scenario).

    Memory [test-migrations-against-old-db-shape].
    """
    db_path = tmp_path / "census.db"
    # Create the DB with only the original tables (no character_aas).
    conn = sqlite3.connect(db_path)
    conn.execute(cs._CREATE_CHARACTERS)
    conn.execute(cs._CREATE_GUILDS)
    conn.commit()
    conn.close()

    # Now run init_db — should add character_aas without raising.
    conn = cs.init_db(db_path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "character_aas" in tables
    finally:
        conn.close()
