"""Tests for the AA endpoint's census_store integration (Task 2c.6).

Verifies:
  - Cold cache: Census is called + data is persisted to census_store.
  - Warm store: census_store is served without calling Census.
  - Stale store record: census_store is served immediately, background
    refresh is spawned.
  - The migration-owned census schema carries the character_aas table
    (init_db creates nothing).
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.census import store as cs
from tests.fixtures.pg import pg_conn

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
async def test_cold_cache_calls_census_and_persists(app, census_schema):
    """On first fetch (no cache, no store), Census is queried and the result
    is written to census_store."""
    fake_aas = _make_census_aas("Coldchar")

    with (
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient") as MockCC,
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
    conn = cs.CensusStore(census_schema).init_db()
    try:
        rec = cs.CensusStore.get_character_aas(conn, "Coldchar", "Varsoon")
        assert rec is not None
        assert rec["data"]["character_name"] == "Coldchar"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Warm-store test: census_store served without calling Census.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warm_store_skips_census(app, census_schema):
    """When census_store has a fresh record, Census is NOT called."""
    # Pre-seed the store with a recent timestamp.
    conn = cs.CensusStore(census_schema).init_db()
    stored_data = {
        "character_name": "Stored",
        "total_spent": 10,
        "trees": [],
        "profiles": [],
    }
    cs.CensusStore.upsert_character_aas(conn, "Stored", "Varsoon", stored_data, now=int(time.time()))
    conn.close()

    with (
        patch("backend.server.api.aa.aa_cache") as mock_cache,
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient") as MockCC,
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
async def test_stale_store_returns_data_and_spawns_refresh(app, census_schema):
    """A store record older than CHARACTER_STALE_S is served immediately but
    triggers a background refresh task."""
    from backend.server.constants import CHARACTER_STALE_S

    old_ts = int(time.time()) - CHARACTER_STALE_S - 60  # definitely stale
    conn = cs.CensusStore(census_schema).init_db()
    stored_data = {
        "character_name": "Stalechar",
        "total_spent": 7,
        "trees": [],
        "profiles": [],
    }
    cs.CensusStore.upsert_character_aas(conn, "Stalechar", "Varsoon", stored_data, now=old_ts)
    conn.close()

    tasks_created: list = []

    def _fake_create_task(coro):
        tasks_created.append(coro)
        coro.close()  # never run it — just silence the un-awaited warning
        return MagicMock()

    with (
        patch("backend.server.api.aa.aa_cache") as mock_cache,
        patch("backend.server.api.aa.asyncio.create_task", side_effect=_fake_create_task),
        patch("backend.server.core.census_lifecycle._clients", {}),
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
# Schema invariant: the migrations own the census DDL — the character_aas
# table must exist in any freshly-built census schema
# (store.init_db() creates nothing; db/migrations/0003_census.sql is
# the single source of schema truth).
# ---------------------------------------------------------------------------


def test_census_schema_has_character_aas_table(census_schema):
    with pg_conn(census_schema) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (census_schema,),
        ).fetchall()
    assert "character_aas" in {r["table_name"] for r in rows}
