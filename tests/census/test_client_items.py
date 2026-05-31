"""Tests for census.client item-related dispatch — COV-009 sub 1.

Covers: get_item (name/ID/game-link dispatch), _find_in_db, _cache_item,
get_raw_item.

All Census HTTP calls are mocked — no real network traffic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from backend.census.client import CensusClient


@pytest.fixture
def client():
    """Bare CensusClient — sessions are mocked per-test."""
    return CensusClient(service_id="test-key")


# ---------------------------------------------------------------------------
# get_item
# ---------------------------------------------------------------------------


class TestGetItem:
    @pytest.mark.asyncio
    async def test_local_db_hit_returns_parsed_item_without_census(self, client):
        """When _find_in_db returns a row, Census is never contacted."""
        fake_raw = {
            "id": "1",
            "displayname": "Iron Helm",
            "tier": "Common",
            "typeinfo": {"name": "armor", "display_name": "Armor"},
            "stat_list": [],
            "item_type_list": [],
            "slot_list": [],
            "effect_list": [],
            "set_data": None,
        }
        with (
            patch.object(client, "_find_in_db", new=AsyncMock(return_value=fake_raw)),
            patch.object(client, "_fetch", new=AsyncMock(side_effect=AssertionError("must not be called"))),
        ):
            result = await client.get_item("Iron Helm")
        assert result is not None
        assert result.name == "Iron Helm"

    @pytest.mark.asyncio
    async def test_cache_miss_falls_back_to_census(self, client):
        """When local DB misses, Census is queried and the item is cached."""
        fake_raw = {
            "id": "2",
            "displayname": "Steel Sword",
            "tier": "Common",
            "typeinfo": {"name": "weapon", "display_name": "Weapon"},
            "stat_list": [],
            "item_type_list": [],
            "slot_list": [],
            "effect_list": [],
            "set_data": None,
        }
        census_response = {"item_list": [fake_raw]}
        cache_mock = MagicMock()

        with (
            patch.object(client, "_find_in_db", new=AsyncMock(return_value=None)),
            patch.object(client, "_fetch", new=AsyncMock(return_value=census_response)),
            patch.object(client, "_cache_item", cache_mock),
        ):
            result = await client.get_item("Steel Sword")

        assert result is not None
        assert result.name == "Steel Sword"
        cache_mock.assert_called_once_with(fake_raw)

    @pytest.mark.asyncio
    async def test_census_miss_returns_none(self, client):
        """When both local DB and Census return nothing, returns None."""
        with (
            patch.object(client, "_find_in_db", new=AsyncMock(return_value=None)),
            patch.object(client, "_fetch", new=AsyncMock(return_value={"item_list": []})),
        ):
            result = await client.get_item("Nonexistent Item")
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_returns_none_returns_none(self, client):
        with (
            patch.object(client, "_find_in_db", new=AsyncMock(return_value=None)),
            patch.object(client, "_fetch", new=AsyncMock(return_value=None)),
        ):
            result = await client.get_item("Gone")
        assert result is None


# ---------------------------------------------------------------------------
# _find_in_db
# ---------------------------------------------------------------------------


class TestFindInDB:
    @pytest.mark.asyncio
    async def test_numeric_id_dispatches_to_find_by_id(self, client):
        with patch("backend.census.client.item_db.find_by_id", new=AsyncMock(return_value=None)) as mock_find:
            await client._find_in_db("12345")
        mock_find.assert_called_once_with(12345)

    @pytest.mark.asyncio
    async def test_negative_numeric_id_dispatches_to_find_by_id(self, client):
        with patch("backend.census.client.item_db.find_by_id", new=AsyncMock(return_value=None)) as mock_find:
            await client._find_in_db("-100")
        mock_find.assert_called_once_with(-100)

    @pytest.mark.asyncio
    async def test_game_link_extracts_unsigned_id_from_negative(self, client):
        """Negative signed ID in game link → unsigned (add 2**32)."""
        expected_id = 2**32 - 100
        with patch("backend.census.client.item_db.find_by_id", new=AsyncMock(return_value=None)) as mock_find:
            await client._find_in_db(r"\aITEM -100 Raw Lead:Raw Lead/a")
        mock_find.assert_called_once_with(expected_id)

    @pytest.mark.asyncio
    async def test_game_link_positive_id_stays_positive(self, client):
        with patch("backend.census.client.item_db.find_by_id", new=AsyncMock(return_value=None)) as mock_find:
            await client._find_in_db(r"\aITEM 500 Raw Lead:Raw Lead/a")
        mock_find.assert_called_once_with(500)

    @pytest.mark.asyncio
    async def test_display_name_dispatches_to_find_by_name(self, client):
        with patch("backend.census.client.item_db.find_by_name", new=AsyncMock(return_value=None)) as mock_find:
            await client._find_in_db("Raw Lead")
        mock_find.assert_called_once_with("Raw Lead")


# ---------------------------------------------------------------------------
# _cache_item
# ---------------------------------------------------------------------------


class TestCacheItem:
    def test_cache_item_calls_upsert(self, client):
        fake_raw = {"id": 1, "displayname": "Test Item"}
        init_mock = MagicMock()
        conn_mock = MagicMock()
        init_mock.return_value = conn_mock

        with (
            patch("backend.census.client.item_db.init_db", init_mock),
            patch("backend.census.client.item_db.upsert_items") as upsert_mock,
        ):
            client._cache_item(fake_raw)

        upsert_mock.assert_called_once_with([fake_raw], conn_mock)
        conn_mock.close.assert_called_once()

    def test_cache_item_swallows_db_error(self, client):
        """DB errors in caching must not propagate."""
        with patch("backend.census.client.item_db.init_db", side_effect=Exception("DB exploded")):
            # Should not raise
            client._cache_item({"id": 1, "displayname": "Item"})


# ---------------------------------------------------------------------------
# get_raw_item
# ---------------------------------------------------------------------------


class TestGetRawItem:
    @pytest.mark.asyncio
    async def test_returns_fetch_response(self, client):
        expected = {"item_list": [{"id": 1, "displayname": "Raw Lead"}]}
        with patch.object(client, "_fetch", new=AsyncMock(return_value=expected)):
            result = await client.get_raw_item("Raw Lead")
        assert result == expected

    @pytest.mark.asyncio
    async def test_returns_none_on_fetch_failure(self, client):
        with patch.object(client, "_fetch", new=AsyncMock(return_value=None)):
            result = await client.get_raw_item("Missing")
        assert result is None
