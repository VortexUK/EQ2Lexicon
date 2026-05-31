"""Tests for web.lib.primary_guild — COV-021.

Covers get_primary_claim (pure logic) and cached_primary_guild (async,
stubs get_active_claims + character_cache).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.server.core.primary_guild import cached_primary_guild, get_primary_claim


class TestGetPrimaryClaim:
    def test_returns_primary_flagged_claim(self):
        payload = {
            "approved": [
                {"id": 1, "character_name": "Sihtric", "is_primary": False},
                {"id": 2, "character_name": "Menludiir", "is_primary": True},
            ]
        }
        result = get_primary_claim(payload)
        assert result is not None
        assert result["character_name"] == "Menludiir"

    def test_returns_none_when_no_primary_flagged(self):
        payload = {
            "approved": [
                {"id": 1, "character_name": "Sihtric", "is_primary": False},
            ]
        }
        result = get_primary_claim(payload)
        assert result is None

    def test_returns_none_when_approved_empty(self):
        result = get_primary_claim({"approved": []})
        assert result is None

    def test_returns_none_when_approved_missing(self):
        result = get_primary_claim({})
        assert result is None

    def test_returns_first_primary_when_multiple_flagged(self):
        # Edge case: two primaries; function returns the first one found
        payload = {
            "approved": [
                {"id": 1, "character_name": "First", "is_primary": True},
                {"id": 2, "character_name": "Second", "is_primary": True},
            ]
        }
        result = get_primary_claim(payload)
        assert result["character_name"] == "First"


class TestCachedPrimaryGuild:
    @pytest.mark.asyncio
    async def test_returns_character_and_guild_from_cache(self):
        claims_payload = {
            "approved": [
                {"id": 1, "character_name": "Sihtric", "is_primary": True},
            ]
        }
        fake_cached_char = MagicMock()
        fake_cached_char.guild_name = "Exordium"

        with (
            patch("backend.server.core.primary_guild.get_active_claims", new=AsyncMock(return_value=claims_payload)),
            patch("backend.server.core.primary_guild.character_cache") as mock_cache,
        ):
            mock_cache.get_stale.return_value = (fake_cached_char, False)
            char_name, guild_name = await cached_primary_guild("user-1", "Varsoon")

        assert char_name == "Sihtric"
        assert guild_name == "Exordium"

    @pytest.mark.asyncio
    async def test_returns_none_guild_when_not_in_cache(self):
        claims_payload = {
            "approved": [
                {"id": 1, "character_name": "Sihtric", "is_primary": True},
            ]
        }
        with (
            patch("backend.server.core.primary_guild.get_active_claims", new=AsyncMock(return_value=claims_payload)),
            patch("backend.server.core.primary_guild.character_cache") as mock_cache,
        ):
            mock_cache.get_stale.return_value = (None, False)
            char_name, guild_name = await cached_primary_guild("user-1", "Varsoon")

        assert char_name == "Sihtric"
        assert guild_name is None

    @pytest.mark.asyncio
    async def test_returns_none_both_when_no_primary_claim(self):
        with (
            patch("backend.server.core.primary_guild.get_active_claims", new=AsyncMock(return_value={"approved": []})),
        ):
            char_name, guild_name = await cached_primary_guild("user-1", "Varsoon")

        assert char_name is None
        assert guild_name is None

    @pytest.mark.asyncio
    async def test_returns_none_guild_when_cached_char_has_no_guild(self):
        claims_payload = {
            "approved": [
                {"id": 1, "character_name": "Sihtric", "is_primary": True},
            ]
        }
        fake_cached_char = MagicMock()
        fake_cached_char.guild_name = None

        with (
            patch("backend.server.core.primary_guild.get_active_claims", new=AsyncMock(return_value=claims_payload)),
            patch("backend.server.core.primary_guild.character_cache") as mock_cache,
        ):
            mock_cache.get_stale.return_value = (fake_cached_char, False)
            char_name, guild_name = await cached_primary_guild("user-1", "Varsoon")

        assert char_name == "Sihtric"
        assert guild_name is None
