"""Tests for the GET /api/character/{name}/spells endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.eq2db.spells import Blocklist
from backend.server.api.character import CharacterResponse, CharacterStats

_EMPTY_BLOCKLIST = Blocklist(frozenset(), [])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_char(name: str = "Sihtric", spell_ids: list[int] | None = None) -> CharacterResponse:
    """Build a minimal CharacterResponse."""
    return CharacterResponse(
        id="123",
        name=name,
        level=100,
        cls="Shadowknight",
        world="Varsoon",
        spell_ids=spell_ids or [],
    )


def _fake_spell_row(
    spell_id: int,
    name: str = "Test Spell I",
    tier_name: str = "Adept",
    spell_type: str = "spells",
    level: int = 20,
    given_by: str = "spellscroll",
    icon_id: int | None = 500,
    icon_backdrop: int | None = 456,
) -> dict:
    """Build a minimal spell DB row dict."""
    return {
        "id": spell_id,
        "name": name,
        "base_name": name.rstrip("I").rstrip(),
        "tier": 3,
        "tier_name": tier_name,
        "type": spell_type,
        "level": level,
        "given_by": given_by,
        "icon_id": icon_id,
        "icon_backdrop": icon_backdrop,
        "passes_spellcheck": 1,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spells_db_not_available(app):
    """503 when spells DB doesn't exist."""
    mock_db = MagicMock()
    mock_db.exists.return_value = False

    with patch("backend.server.api.character.spells._SPELLS_DB", mock_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Sihtric/spells")

    assert r.status_code == 503


@pytest.mark.asyncio
async def test_spells_character_not_found(app):
    """404 when character not found and DB exists."""
    mock_db = MagicMock()
    mock_db.exists.return_value = True

    mock_census = AsyncMock()
    mock_census.get_character = AsyncMock(return_value=None)

    with (
        patch("backend.server.api.character.spells._SPELLS_DB", mock_db),
        patch("backend.server.api.character.spells.character_cache") as mock_cache,
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient", return_value=mock_census),
    ):
        # Cache miss
        mock_cache.get_stale.return_value = (None, False)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Sihtric/spells")

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_spells_returns_empty_for_no_spell_ids(app):
    """Returns empty spells list when character has no spell_ids."""
    mock_db = MagicMock()
    mock_db.exists.return_value = True

    char = _fake_char(spell_ids=[])

    with (
        patch("backend.server.api.character.spells._SPELLS_DB", mock_db),
        patch("backend.server.api.character.spells.character_cache") as mock_cache,
    ):
        mock_cache.get_stale.return_value = (char, False)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Sihtric/spells")

    assert r.status_code == 200
    data = r.json()
    assert data["spells"] == []
    assert data["tiers_present"] == []


@pytest.mark.asyncio
async def test_spells_returns_data(app):
    """Happy path: cached character with spell IDs → resolved spells returned."""
    mock_db = MagicMock()
    mock_db.exists.return_value = True

    char = _fake_char(spell_ids=[1001, 1002])
    spell_rows = {
        1001: _fake_spell_row(1001, name="Wound I", tier_name="Adept", level=20),
        1002: _fake_spell_row(1002, name="Lifetap I", tier_name="Master", level=30),
    }

    with (
        patch("backend.server.api.character.spells._SPELLS_DB", mock_db),
        patch("backend.server.api.character.spells.character_cache") as mock_cache,
        patch("backend.server.api.character.spells._spell_find_by_ids", return_value=spell_rows),
        patch("backend.server.api.character.spells._load_spell_blocklist", return_value=_EMPTY_BLOCKLIST),
    ):
        mock_cache.get_stale.return_value = (char, False)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Sihtric/spells")

    assert r.status_code == 200
    data = r.json()
    assert data["character_name"] == "Sihtric"
    spell_names = {s["name"] for s in data["spells"]}
    assert "Wound I" in spell_names
    assert "Lifetap I" in spell_names
    assert "Adept" in data["tier_counts"]
    assert data["tier_counts"]["Adept"] == 1
    assert data["tier_counts"]["Master"] == 1
    assert "Adept" in data["tiers_present"]
    assert "Master" in data["tiers_present"]


@pytest.mark.asyncio
async def test_spells_blocklist_applied(app):
    """Blocked spells are excluded from the response."""
    mock_db = MagicMock()
    mock_db.exists.return_value = True

    char = _fake_char(spell_ids=[2001, 2002])
    spell_rows = {
        2001: _fake_spell_row(2001, name="Fighting Chance I", tier_name="Adept", level=10),
        2002: _fake_spell_row(2002, name="Lifetap I", tier_name="Adept", level=20),
    }

    # "fighting chance" (stripped roman) is blocklisted
    blocklist = Blocklist(frozenset({"fighting chance"}), [])

    with (
        patch("backend.server.api.character.spells._SPELLS_DB", mock_db),
        patch("backend.server.api.character.spells.character_cache") as mock_cache,
        patch("backend.server.api.character.spells._spell_find_by_ids", return_value=spell_rows),
        patch("backend.server.api.character.spells._load_spell_blocklist", return_value=blocklist),
    ):
        mock_cache.get_stale.return_value = (char, False)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Sihtric/spells")

    assert r.status_code == 200
    data = r.json()
    spell_names = {s["name"] for s in data["spells"]}
    assert "Fighting Chance I" not in spell_names
    assert "Lifetap I" in spell_names


@pytest.mark.asyncio
async def test_spells_only_includes_spellscroll(app):
    """Only given_by='spellscroll' entries are included; class/aa/any are excluded."""
    mock_db = MagicMock()
    mock_db.exists.return_value = True

    char = _fake_char(spell_ids=[3001, 3002, 3003])
    spell_rows = {
        3001: _fake_spell_row(
            3001, name="Cheap Shot I", tier_name="Adept", spell_type="arts", level=15, given_by="class"
        ),
        3002: _fake_spell_row(3002, name="AA Spell I", tier_name="Adept", level=50, given_by="alternateadvancement"),
        3003: _fake_spell_row(3003, name="Scribed Spell I", tier_name="Adept", level=30, given_by="spellscroll"),
    }

    with (
        patch("backend.server.api.character.spells._SPELLS_DB", mock_db),
        patch("backend.server.api.character.spells.character_cache") as mock_cache,
        patch("backend.server.api.character.spells._spell_find_by_ids", return_value=spell_rows),
        patch("backend.server.api.character.spells._load_spell_blocklist", return_value=_EMPTY_BLOCKLIST),
    ):
        mock_cache.get_stale.return_value = (char, False)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Sihtric/spells")

    assert r.status_code == 200
    data = r.json()
    spell_names = {s["name"] for s in data["spells"]}
    assert "Cheap Shot I" not in spell_names
    assert "AA Spell I" not in spell_names
    assert "Scribed Spell I" in spell_names


@pytest.mark.asyncio
async def test_spells_excludes_zero_level(app):
    """Spells with level=0 are excluded."""
    mock_db = MagicMock()
    mock_db.exists.return_value = True

    char = _fake_char(spell_ids=[5001, 5002])
    spell_rows = {
        5001: _fake_spell_row(5001, name="Zero Level Spell", tier_name="Adept", level=0),
        5002: _fake_spell_row(5002, name="Normal Spell I", tier_name="Adept", level=10),
    }

    with (
        patch("backend.server.api.character.spells._SPELLS_DB", mock_db),
        patch("backend.server.api.character.spells.character_cache") as mock_cache,
        patch("backend.server.api.character.spells._spell_find_by_ids", return_value=spell_rows),
        patch("backend.server.api.character.spells._load_spell_blocklist", return_value=_EMPTY_BLOCKLIST),
    ):
        mock_cache.get_stale.return_value = (char, False)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Sihtric/spells")

    assert r.status_code == 200
    data = r.json()
    spell_names = {s["name"] for s in data["spells"]}
    assert "Zero Level Spell" not in spell_names
    assert "Normal Spell I" in spell_names


@pytest.mark.asyncio
async def test_spells_deduplication_keeps_highest_level(app):
    """Duplicate base names keep only the highest-level entry."""
    mock_db = MagicMock()
    mock_db.exists.return_value = True

    char = _fake_char(spell_ids=[6001, 6002, 6003])
    spell_rows = {
        6001: _fake_spell_row(6001, name="Fireball I", tier_name="Apprentice", level=10),
        6002: _fake_spell_row(6002, name="Fireball II", tier_name="Adept", level=20),
        6003: _fake_spell_row(6003, name="Fireball III", tier_name="Master", level=30),
    }

    with (
        patch("backend.server.api.character.spells._SPELLS_DB", mock_db),
        patch("backend.server.api.character.spells.character_cache") as mock_cache,
        patch("backend.server.api.character.spells._spell_find_by_ids", return_value=spell_rows),
        patch("backend.server.api.character.spells._load_spell_blocklist", return_value=_EMPTY_BLOCKLIST),
    ):
        mock_cache.get_stale.return_value = (char, False)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Sihtric/spells")

    assert r.status_code == 200
    data = r.json()
    # Only one entry should remain after deduplication
    fireball_entries = [s for s in data["spells"] if "Fireball" in s["name"]]
    assert len(fireball_entries) == 1
    assert fireball_entries[0]["level"] == 30


@pytest.mark.asyncio
async def test_spells_fetches_from_census_on_cache_miss(app):
    """Falls back to Census API when character is not in cache."""
    mock_db = MagicMock()
    mock_db.exists.return_value = True

    from backend.census.models import CharacterOverview

    mock_char_overview = CharacterOverview(
        id="999",
        name="Menludiir",
        level=90,
        cls="Wizard",
        race="High Elf",
        gender="Male",
        deity=None,
        aa_count=0,
        world="Varsoon",
        spell_ids=[],
    )

    mock_census = AsyncMock()
    mock_census.get_character = AsyncMock(return_value=mock_char_overview)

    with (
        patch("backend.server.api.character.spells._SPELLS_DB", mock_db),
        patch("backend.server.api.character.spells.character_cache") as mock_cache,
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient", return_value=mock_census),
    ):
        # Cache miss
        mock_cache.get_stale.return_value = (None, False)
        mock_cache.set = MagicMock()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Menludiir/spells")

    assert r.status_code == 200
    data = r.json()
    assert data["character_name"] == "Menludiir"
    assert data["spells"] == []
    # Verify the cache was populated
    mock_cache.set.assert_called_once()


@pytest.mark.asyncio
async def test_spells_response_structure(app):
    """Response has all required fields with correct types."""
    mock_db = MagicMock()
    mock_db.exists.return_value = True

    char = _fake_char(spell_ids=[7001])
    spell_rows = {
        7001: _fake_spell_row(7001, name="Heal I", tier_name="Adept", level=10),
    }

    with (
        patch("backend.server.api.character.spells._SPELLS_DB", mock_db),
        patch("backend.server.api.character.spells.character_cache") as mock_cache,
        patch("backend.server.api.character.spells._spell_find_by_ids", return_value=spell_rows),
        patch("backend.server.api.character.spells._load_spell_blocklist", return_value=_EMPTY_BLOCKLIST),
    ):
        mock_cache.get_stale.return_value = (char, False)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Sihtric/spells")

    assert r.status_code == 200
    data = r.json()

    # Top-level keys
    assert "character_name" in data
    assert "spells" in data
    assert "tier_counts" in data
    assert "tiers_present" in data

    # Spell entry fields
    spell = data["spells"][0]
    assert "name" in spell
    assert "tier" in spell
    assert "level" in spell
    assert "spell_type" in spell

    # tier_counts has all SPELL_TIER_ORDER keys
    from backend.census.constants import SPELL_TIER_ORDER

    for tier in SPELL_TIER_ORDER:
        assert tier in data["tier_counts"]
