"""Tests for census.client character-related methods — COV-009 sub 3.

Covers: get_character, get_character_aas, get_character_brief,
get_character_spells, get_character_guild_name.

All Census HTTP calls are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.census.client import CensusClient


@pytest.fixture
def client():
    return CensusClient(service_id="test-key")


def _minimal_char(name: str = "Sihtric", cls: str = "Templar") -> dict:
    return {
        "id": "12345",
        "name": {"first": name},
        "type": {
            "level": "90",
            "class": cls,
            "race": "Kerra",
            "gender": "male",
            "ts_class": "Armorer",
            "ts_level": "100",
            "aa_level": "320",
            "deity": "Mithaniel Marr",
        },
        "stats": {},
        "equipmentslot_list": [],
        "spell_list": [],
        "guild": {"name": "Exordium"},
    }


# ---------------------------------------------------------------------------
# get_character
# ---------------------------------------------------------------------------


class TestGetCharacter:
    @pytest.mark.asyncio
    async def test_returns_none_when_census_unavailable(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value=None)):
            result = await client.get_character("Sihtric", "Varsoon")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_char_list(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value={"character_list": []})):
            result = await client.get_character("Unknown", "Varsoon")
        assert result is None

    @pytest.mark.asyncio
    async def test_parses_character_name_and_class(self, client):
        census_data = {"character_list": [_minimal_char("Sihtric", "Templar")]}
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_character("Sihtric", "Varsoon")
        assert result is not None
        assert result.name == "Sihtric"
        assert result.cls == "Templar"

    @pytest.mark.asyncio
    async def test_extracts_guild_name(self, client):
        census_data = {"character_list": [_minimal_char()]}
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_character("Sihtric", "Varsoon")
        assert result.guild_name == "Exordium"

    @pytest.mark.asyncio
    async def test_none_deity_string_becomes_python_none(self, client):
        char = _minimal_char()
        char["type"]["deity"] = "None"
        census_data = {"character_list": [char]}
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_character("Sihtric", "Varsoon")
        assert result.deity is None

    @pytest.mark.asyncio
    async def test_extracts_spell_ids(self, client):
        char = _minimal_char()
        char["spell_list"] = [{"id": "111"}, {"id": "222"}]
        census_data = {"character_list": [char]}
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_character("Sihtric", "Varsoon")
        assert 111 in result.spell_ids
        assert 222 in result.spell_ids


# ---------------------------------------------------------------------------
# get_character_aas
# ---------------------------------------------------------------------------


class TestGetCharacterAAs:
    @pytest.mark.asyncio
    async def test_returns_none_when_census_unavailable(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value=None)):
            result = await client.get_character_aas("Sihtric", "Varsoon")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_char_list(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value={"character_list": []})):
            result = await client.get_character_aas("Unknown", "Varsoon")
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_aa_entries_with_tier_zero(self, client):
        census_data = {
            "character_list": [
                {
                    "name": {"first": "Sihtric"},
                    "alternateadvancements": {
                        "alternateadvancement_list": [
                            {"id": "1", "treeID": "10", "tier": "0"},  # should be skipped
                            {"id": "2", "treeID": "10", "tier": "3"},  # should be kept
                        ]
                    },
                    "orderedalternateadvancement_list": [],
                }
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_character_aas("Sihtric", "Varsoon")
        assert len(result.aa_list) == 1
        assert result.aa_list[0].tier == 3

    @pytest.mark.asyncio
    async def test_parses_profiles_from_ordered_list(self, client):
        census_data = {
            "character_list": [
                {
                    "name": {"first": "Sihtric"},
                    "alternateadvancements": {"alternateadvancement_list": []},
                    "orderedalternateadvancement_list": [
                        {
                            "profilename": "Combat",
                            "alternateadvancement_list": [
                                {"id": "5", "treeID": "20"},
                                {"id": "5", "treeID": "20"},  # same node counted twice = tier 2
                            ],
                        }
                    ],
                }
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_character_aas("Sihtric", "Varsoon")
        assert len(result.profiles) == 1
        assert result.profiles[0].name == "Combat"
        assert result.profiles[0].aa_list[0].tier == 2


# ---------------------------------------------------------------------------
# get_character_brief
# ---------------------------------------------------------------------------


class TestGetCharacterBrief:
    @pytest.mark.asyncio
    async def test_returns_none_when_census_unavailable(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value=None)):
            result = await client.get_character_brief("Sihtric", "Varsoon")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_char_list(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value={"character_list": []})):
            result = await client.get_character_brief("Unknown", "Varsoon")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_name_class_level_guild(self, client):
        census_data = {
            "character_list": [
                {
                    "name": {"first": "Sihtric"},
                    "type": {"class": "Templar", "classid": "10", "level": "90", "aa_level": "320", "race": "Kerra"},
                    "guild": {"name": "Exordium"},
                }
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_character_brief("Sihtric", "Varsoon")
        assert result["name"] == "Sihtric"
        assert result["cls"] == "Templar"
        assert result["level"] == 90
        assert result["guild_name"] == "Exordium"


# ---------------------------------------------------------------------------
# get_character_spells
# ---------------------------------------------------------------------------


class TestGetCharacterSpells:
    @pytest.mark.asyncio
    async def test_returns_none_when_census_unavailable(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value=None)):
            result = await client.get_character_spells("Sihtric", "Varsoon")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_char_list(self, client):
        with patch.object(client, "_census_get", new=AsyncMock(return_value={"character_list": []})):
            result = await client.get_character_spells("Unknown", "Varsoon")
        assert result is None

    @pytest.mark.asyncio
    async def test_filters_level_zero_spells(self, client):
        census_data = {
            "character_list": [
                {
                    "name": {"first": "Sihtric"},
                    "spell_list": [
                        {
                            "name": "Hidden",
                            "tier_name": "Expert",
                            "type": "spells",
                            "level": "0",
                            "given_by": "spellscroll",
                        },
                        {
                            "name": "Visible",
                            "tier_name": "Expert",
                            "type": "spells",
                            "level": "90",
                            "given_by": "spellscroll",
                        },
                    ],
                }
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_character_spells("Sihtric", "Varsoon")
        assert len(result.entries) == 1
        assert result.entries[0].name == "Visible"

    @pytest.mark.asyncio
    async def test_filters_aa_granted_spells(self, client):
        census_data = {
            "character_list": [
                {
                    "name": {"first": "Sihtric"},
                    "spell_list": [
                        {
                            "name": "AA Spell",
                            "tier_name": "Expert",
                            "type": "spells",
                            "level": "90",
                            "given_by": "alternateadvancement",
                        },
                        {
                            "name": "Normal Spell",
                            "tier_name": "Expert",
                            "type": "spells",
                            "level": "90",
                            "given_by": "spellscroll",
                        },
                    ],
                }
            ]
        }
        with patch.object(client, "_census_get", new=AsyncMock(return_value=census_data)):
            result = await client.get_character_spells("Sihtric", "Varsoon")
        names = [e.name for e in result.entries]
        assert "AA Spell" not in names
        assert "Normal Spell" in names


# ---------------------------------------------------------------------------
# get_character_guild_name
# ---------------------------------------------------------------------------


class TestGetCharacterGuildName:
    @pytest.mark.asyncio
    async def test_returns_guild_name_on_success(self, client):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"character_list": [{"guild": {"name": "Exordium"}}]})
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_ctx)

        with patch.object(client, "_session_", return_value=mock_session):
            result = await client.get_character_guild_name("Sihtric", "Varsoon")
        assert result == "Exordium"

    @pytest.mark.asyncio
    async def test_returns_none_when_character_not_found(self, client):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"character_list": []})
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_ctx)

        with patch.object(client, "_session_", return_value=mock_session):
            result = await client.get_character_guild_name("Ghost", "Varsoon")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_character_has_no_guild(self, client):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"character_list": [{"guild": None}]})
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_ctx)

        with patch.object(client, "_session_", return_value=mock_session):
            result = await client.get_character_guild_name("Loner", "Varsoon")
        assert result is None

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self, client):
        """Non-200 HTTP response should raise (not return None)."""
        mock_resp = MagicMock()
        mock_resp.status = 503
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_ctx)

        with patch.object(client, "_session_", return_value=mock_session):
            with pytest.raises(Exception):
                await client.get_character_guild_name("Sihtric", "Varsoon")
