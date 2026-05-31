"""Tests for census.client search methods — COV-009 sub 4.

Covers: search_characters (parallel fan-out, dedup, sort), search_characters_by_name,
search_guilds_by_name, _search_chars_single (CensusError on failure).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.census.client import CensusClient, CensusError


@pytest.fixture
def client():
    return CensusClient(service_id="test-key")


def _char_result(name: str, level: int = 50, aa: int = 100) -> dict:
    return {
        "name": name,
        "cls": "Templar",
        "class_id": 10,
        "level": level,
        "aa_level": aa,
        "race": "Kerra",
        "guild_name": None,
    }


# ---------------------------------------------------------------------------
# search_characters_by_name
# ---------------------------------------------------------------------------


class TestSearchCharactersByName:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_census_unavailable(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value=None)):
            result = await client.search_characters_by_name("Sih", "Varsoon")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_no_results(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value={"character_list": []})):
            result = await client.search_characters_by_name("Zzz", "Varsoon")
        assert result == []

    @pytest.mark.asyncio
    async def test_parses_results_and_sorts_by_name(self, client):
        census_data = {
            "character_list": [
                {
                    "name": {"first": "Zephyr"},
                    "type": {"class": "Ranger", "classid": "5", "level": "90", "aa_level": "200", "race": "Elf"},
                    "guild": {},
                },
                {
                    "name": {"first": "Alarin"},
                    "type": {"class": "Templar", "classid": "10", "level": "80", "aa_level": "100", "race": "Human"},
                    "guild": {},
                },
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.search_characters_by_name("a", "Varsoon")
        assert result[0]["name"] == "Alarin"
        assert result[1]["name"] == "Zephyr"

    @pytest.mark.asyncio
    async def test_skips_entries_with_no_name(self, client):
        census_data = {
            "character_list": [
                {"name": {}, "type": {}, "guild": {}},  # no "first" key
                {
                    "name": {"first": "Valid"},
                    "type": {"class": "T", "classid": "1", "level": "1", "aa_level": "0", "race": "H"},
                    "guild": {},
                },
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.search_characters_by_name("v", "Varsoon")
        assert all(r["name"] for r in result)


# ---------------------------------------------------------------------------
# search_guilds_by_name
# ---------------------------------------------------------------------------


class TestSearchGuildsByName:
    @pytest.mark.asyncio
    async def test_returns_empty_when_census_unavailable(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value=None)):
            result = await client.search_guilds_by_name("Ex", "Varsoon")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_sorted_guild_names(self, client):
        census_data = {
            "guild_list": [
                {"name": "Zeta Guild"},
                {"name": "Alpha Guild"},
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.search_guilds_by_name("a", "Varsoon")
        names = [r["name"] for r in result]
        assert names == sorted(names, key=str.lower)

    @pytest.mark.asyncio
    async def test_skips_guilds_with_no_name(self, client):
        census_data = {
            "guild_list": [
                {},  # no "name" key
                {"name": "Valid Guild"},
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.search_guilds_by_name("v", "Varsoon")
        assert len(result) == 1
        assert result[0]["name"] == "Valid Guild"


# ---------------------------------------------------------------------------
# search_characters (fan-out, dedup, sort)
# ---------------------------------------------------------------------------


class TestSearchCharacters:
    @pytest.mark.asyncio
    async def test_single_class_id_returns_results(self, client):
        single_result = [_char_result("Sihtric", level=90, aa=320)]
        with patch.object(client, "_search_chars_single", new=AsyncMock(return_value=single_result)):
            result = await client.search_characters("Varsoon", class_ids=[10])
        assert result["total"] == 1
        assert result["results"][0]["name"] == "Sihtric"

    @pytest.mark.asyncio
    async def test_deduplicates_results_across_parallel_calls(self, client):
        """When two class_ids return the same character, it's included once."""
        shared_result = [_char_result("Sihtric")]
        with patch.object(client, "_search_chars_single", new=AsyncMock(return_value=shared_result)):
            result = await client.search_characters("Varsoon", class_ids=[10, 11])
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_sorts_by_level_descending_by_default(self, client):
        raw = [
            _char_result("Low", level=10),
            _char_result("High", level=90),
            _char_result("Mid", level=50),
        ]
        with patch.object(client, "_search_chars_single", new=AsyncMock(return_value=raw)):
            result = await client.search_characters("Varsoon", class_ids=[])
        levels = [r["level"] for r in result["results"]]
        assert levels == sorted(levels, reverse=True)

    @pytest.mark.asyncio
    async def test_sorts_by_name_ascending(self, client):
        raw = [_char_result("Zack"), _char_result("Alice"), _char_result("Bob")]
        with patch.object(client, "_search_chars_single", new=AsyncMock(return_value=raw)):
            result = await client.search_characters("Varsoon", class_ids=[], sort_by="name", sort_dir="asc")
        names = [r["name"] for r in result["results"]]
        assert names == sorted(names)

    @pytest.mark.asyncio
    async def test_sorts_by_aa_descending(self, client):
        raw = [_char_result("Low", aa=10), _char_result("High", aa=500), _char_result("Mid", aa=200)]
        with patch.object(client, "_search_chars_single", new=AsyncMock(return_value=raw)):
            result = await client.search_characters("Varsoon", class_ids=[], sort_by="aa")
        aas = [r["aa_level"] for r in result["results"]]
        assert aas == sorted(aas, reverse=True)

    @pytest.mark.asyncio
    async def test_applies_max_level_filter(self, client):
        raw = [_char_result("Young", level=20), _char_result("Old", level=90)]
        with patch.object(client, "_search_chars_single", new=AsyncMock(return_value=raw)):
            result = await client.search_characters("Varsoon", class_ids=[], max_level=50)
        assert all(r["level"] <= 50 for r in result["results"])

    @pytest.mark.asyncio
    async def test_paginates_results(self, client):
        raw = [_char_result(f"Char{i}", level=i) for i in range(10, 0, -1)]
        with patch.object(client, "_search_chars_single", new=AsyncMock(return_value=raw)):
            result = await client.search_characters("Varsoon", class_ids=[], per_page=3, page=2)
        assert len(result["results"]) == 3
        assert result["total"] == 10
        assert result["page"] == 2


# ---------------------------------------------------------------------------
# _search_chars_single (COV-036: raises CensusError on census failure)
# ---------------------------------------------------------------------------


class TestSearchCharsSingle:
    @pytest.mark.asyncio
    async def test_raises_census_error_on_census_failure(self, client):
        """When _census_get returns None, _search_chars_single raises CensusError."""
        with patch.object(client, "_census_get", new=AsyncMock(return_value=None)):
            with pytest.raises(CensusError):
                await client._search_chars_single("Varsoon", class_id=10, min_level=None)

    @pytest.mark.asyncio
    async def test_returns_list_on_success(self, client):
        census_data = {
            "character_list": [
                {
                    "displayname": "Sihtric",
                    "type": {"class": "T", "classid": "1", "level": "90", "aa_level": "320", "race": "Kerra"},
                    "guild": {"name": "Exordium"},
                },
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client._search_chars_single("Varsoon", class_id=10, min_level=None)
        assert len(result) == 1
        assert result[0]["name"] == "Sihtric"
