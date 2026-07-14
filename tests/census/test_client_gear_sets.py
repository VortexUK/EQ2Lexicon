"""Tests for CensusClient.get_gear_sets (the adventure_sets collection).

Census HTTP is mocked via patch.object(client, "_census_get", ...); item
resolution is mocked at _parse_equipment (its behaviour is covered by the
character-equipment tests — gear sets reuse it verbatim).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.census.client import CensusClient
from backend.census.models import EquipmentSlot

_SLOT = EquipmentSlot(slot_name="Head", item_name="Cowl of Fervor", item_id="1001")


@pytest.fixture
def client():
    return CensusClient(service_id="test-key")


class TestGetGearSets:
    @pytest.mark.asyncio
    async def test_returns_none_when_census_unavailable(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value=None)):
            assert await client.get_gear_sets("123") is None

    @pytest.mark.asyncio
    async def test_returns_empty_for_character_without_sets(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value={"adventure_sets_list": []})):
            assert await client.get_gear_sets("123") == []

    @pytest.mark.asyncio
    async def test_parses_sets_and_reuses_equipment_parser(self, client):
        census_data = {
            "adventure_sets_list": [
                {
                    "id": 123,
                    "set_list": [
                        {"name": "DPS", "equipmentslot_list": [{"name": "primary"}]},
                        {"name": "  ", "equipmentslot_list": []},  # blank → fallback label
                        "not-a-dict",  # malformed entry skipped
                    ],
                }
            ]
        }
        with (
            patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)) as mock_get,
            patch.object(client, "_parse_equipment", new=AsyncMock(return_value=[_SLOT])) as mock_parse,
        ):
            sets = await client.get_gear_sets(123)

        assert sets is not None
        assert [s.name for s in sets] == ["DPS", "Unnamed set"]
        assert sets[0].equipment == [_SLOT]
        # The character id must be stringified into the query.
        assert mock_get.await_args.args[1]["id"] == "123"
        # Both real sets went through the shared equipment parser.
        assert mock_parse.await_count == 2
        mock_parse.assert_any_await([{"name": "primary"}])
