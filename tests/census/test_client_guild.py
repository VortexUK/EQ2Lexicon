"""Tests for census.client guild-related methods — COV-009 sub 2.

Covers: get_guild (rank_map building, member filtering), get_guild_full
(member + equipment parsing, roster stubs).

All Census HTTP calls are mocked via patch.object(client, "_census_get", ...).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.census.client import CensusClient


@pytest.fixture
def client():
    return CensusClient(service_id="test-key")


def _rank_list(entries: list[tuple[int, str]]) -> list[dict]:
    return [{"id": str(eid), "name": name} for eid, name in entries]


def _member_with_type(name: str, rank: int = 0, level: int = 50, cls: str = "Templar") -> dict:
    return {
        "name": name,
        "displayname": name,
        "type": {
            "level": str(level),
            "class": cls,
            "ts_class": "",
            "ts_level": "0",
            "aa_level": "100",
            "deity": "Mithaniel Marr",
            "race": "Human",
            "gender": "male",
        },
        "guild": {"rank": str(rank), "status": "100"},
        "playedtime": "1000",
    }


def _member_without_type(name: str) -> dict:
    return {"name": name, "displayname": name, "guild": {"rank": "0"}}


# ---------------------------------------------------------------------------
# get_guild
# ---------------------------------------------------------------------------


class TestGetGuild:
    @pytest.mark.asyncio
    async def test_returns_none_when_census_unavailable(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value=None)):
            result = await client.get_guild("Exordium", "Varsoon")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_guild_list(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value={"guild_list": []})):
            result = await client.get_guild("Exordium", "Varsoon")
        assert result is None

    @pytest.mark.asyncio
    async def test_builds_rank_map_from_rank_list(self, client):
        census_data = {
            "guild_list": [
                {
                    "name": "Exordium",
                    "world": "Varsoon",
                    "rank_list": _rank_list([(0, "Leader"), (1, "Officer"), (2, "Member")]),
                    "member_list": [_member_with_type("Sihtric", rank=0)],
                }
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_guild("Exordium", "Varsoon")
        assert result is not None
        assert result.members[0].rank == "Leader"

    @pytest.mark.asyncio
    async def test_filters_members_without_type_dict(self, client):
        """Members with non-dict 'type' are skipped."""
        census_data = {
            "guild_list": [
                {
                    "name": "Exordium",
                    "world": "Varsoon",
                    "rank_list": _rank_list([(0, "Leader")]),
                    "member_list": [
                        _member_with_type("Sihtric", rank=0),
                        _member_without_type("OfflineMember"),
                    ],
                }
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_guild("Exordium", "Varsoon")
        assert len(result.members) == 1
        assert result.members[0].name == "Sihtric"

    @pytest.mark.asyncio
    async def test_deity_none_string_becomes_python_none(self, client):
        m = _member_with_type("Atheist", rank=0)
        m["type"]["deity"] = "None"
        census_data = {
            "guild_list": [
                {
                    "name": "Guild",
                    "world": "Varsoon",
                    "rank_list": _rank_list([(0, "Leader")]),
                    "member_list": [m],
                }
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_guild("Guild", "Varsoon")
        assert result.members[0].deity is None

    @pytest.mark.asyncio
    async def test_returns_guilddata_with_correct_name(self, client):
        census_data = {
            "guild_list": [
                {
                    "name": "Exordium",
                    "world": "Varsoon",
                    "rank_list": [],
                    "member_list": [],
                }
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_guild("Exordium", "Varsoon")
        assert result.name == "Exordium"
        assert result.world == "Varsoon"


# ---------------------------------------------------------------------------
# get_guild_full
# ---------------------------------------------------------------------------


class TestGetGuildFull:
    @pytest.mark.asyncio
    async def test_returns_none_when_census_unavailable(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value=None)):
            result = await client.get_guild_full("Exordium", "Varsoon")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_guild_list(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value={"guild_list": []})):
            result = await client.get_guild_full("Exordium", "Varsoon")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_tuple_of_four_elements(self, client):
        m = _member_with_type("Sihtric")
        m["equipmentslot_list"] = []
        m["spell_list"] = []
        m["stats"] = {}
        census_data = {
            "guild_list": [
                {
                    "name": "Exordium",
                    "world": "Varsoon",
                    "rank_list": _rank_list([(0, "Leader")]),
                    "member_list": [m],
                }
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_guild_full("Exordium", "Varsoon")
        assert result is not None
        guild_data, overviews, guild_info, roster_stubs = result
        assert guild_data.name == "Exordium"
        assert len(roster_stubs) == 1
        assert roster_stubs[0]["name"] == "Sihtric"

    @pytest.mark.asyncio
    async def test_roster_stubs_include_member_without_type(self, client):
        """Members with no type dict still appear in roster_stubs."""
        offline_member = {"name": "Offline", "guild": {"rank": "2"}}
        online_member = _member_with_type("Online")
        online_member["equipmentslot_list"] = []
        online_member["spell_list"] = []
        online_member["stats"] = {}
        census_data = {
            "guild_list": [
                {
                    "name": "Guild",
                    "world": "Varsoon",
                    "rank_list": _rank_list([(0, "Leader"), (2, "Member")]),
                    "member_list": [online_member, offline_member],
                }
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_guild_full("Guild", "Varsoon")
        _, _, _, roster_stubs = result
        names = [s["name"] for s in roster_stubs]
        assert "Online" in names
        assert "Offline" in names

    @pytest.mark.asyncio
    async def test_extracts_spell_ids_from_member_spell_list(self, client):
        m = _member_with_type("Spellcaster")
        m["equipmentslot_list"] = []
        m["spell_list"] = [{"id": "100"}, {"id": "200"}]
        m["stats"] = {}
        census_data = {
            "guild_list": [
                {
                    "name": "Guild",
                    "world": "Varsoon",
                    "rank_list": [],
                    "member_list": [m],
                }
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_guild_full("Guild", "Varsoon")
        _, overviews, _, _ = result
        assert 100 in overviews[0].spell_ids
        assert 200 in overviews[0].spell_ids
