"""Tests for the `category` field on /api/parses responses.

The field is computed at query time from _classify_zone(row.zone) and
attached to every ParseEncounterSummary. Frontend reads it in Phase 5;
backend ships it from Phase 3 onwards.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.users import make_fake_require_user, make_fake_user
from tests.server._parses_fixtures import _FAKE_ENCOUNTER

_fake_user = make_fake_require_user(make_fake_user(id="123456789"))


@pytest.mark.asyncio
async def test_list_includes_category_on_every_fight(app):
    fake_list_sync = MagicMock(return_value=[dict(_FAKE_ENCOUNTER, combatant_count=2, player_count=1)])

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", fake_list_sync),
        patch("backend.server.api.parses.list._classify_zone", return_value="other"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    assert r.status_code == 200
    data = r.json()
    assert len(data["results"]) == 1
    assert "category" in data["results"][0]


@pytest.mark.asyncio
async def test_raid_zone_classifies_as_raid(app):
    fake_list_sync = MagicMock(
        return_value=[dict(_FAKE_ENCOUNTER, zone="Castle Mistmoore", combatant_count=2, player_count=1)],
    )

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", fake_list_sync),
        patch("backend.server.api.parses.list._classify_zone", return_value="raid"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    assert r.status_code == 200
    assert r.json()["results"][0]["category"] == "raid"


@pytest.mark.asyncio
async def test_dungeon_zone_classifies_as_dungeon(app):
    fake_list_sync = MagicMock(
        return_value=[dict(_FAKE_ENCOUNTER, zone="Halls of Fate", combatant_count=2, player_count=1)],
    )

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", fake_list_sync),
        patch("backend.server.api.parses.list._classify_zone", return_value="dungeon"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    assert r.status_code == 200
    assert r.json()["results"][0]["category"] == "dungeon"


@pytest.mark.asyncio
async def test_unknown_zone_classifies_as_other(app):
    fake_list_sync = MagicMock(
        return_value=[dict(_FAKE_ENCOUNTER, zone=None, combatant_count=2, player_count=1)],
    )

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", fake_list_sync),
        # No patch on _classify_zone — let the real helper run; it returns
        # "other" for None per its own spec, no zones.db needed.
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/parses")
    assert r.status_code == 200
    assert r.json()["results"][0]["category"] == "other"


@pytest.mark.asyncio
async def test_classifier_called_with_row_zone(app):
    fake_list_sync = MagicMock(
        return_value=[dict(_FAKE_ENCOUNTER, zone="Castle Mistmoore", combatant_count=2, player_count=1)],
    )
    fake_classify = MagicMock(return_value="raid")

    with (
        patch("backend.server.api.parses.list._require_user", _fake_user),
        patch("backend.server.api.parses.list._list_encounters_sync", fake_list_sync),
        patch("backend.server.api.parses.list._classify_zone", fake_classify),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/api/parses")

    fake_classify.assert_called_once_with("Castle Mistmoore")
