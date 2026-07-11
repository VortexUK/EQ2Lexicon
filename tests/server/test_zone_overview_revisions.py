"""Tests for GET /api/zones/{zone}/overview/revisions and the revision-on-write
behaviour wired into PUT /api/zones/{zone}/overview."""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.eq2db import raids as raids_db
from backend.server import db as users_db

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _fake_zone(name: str = "The Emerald Halls") -> dict:
    return {
        "name": name,
        "expansion_short": "EoF",
        "expansion_name": "Echoes of Faydwer",
        "bosses": [
            {"encounter_name": "Prince Thirneg", "position": 1, "stage": "First Floor"},
        ],
    }


def _writer_client(app):
    """Override ``require_editor`` so the test acts as an authorised editor."""
    from backend.server.auth_deps import require_editor

    app.dependency_overrides[require_editor] = lambda: {"id": "admin-1", "username": "admin"}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture()
def raids_tmp(tmp_path):
    """Point raid_strategies at a real RaidCatalogue over a temp raids.db.

    The catalogue is a full drop-in for the route module's ``raids_db``
    binding (init_db uses the instance path; SOURCE_* are class attributes;
    the write helpers are staticmethods). Returns the Path so individual
    tests can open it for inspection."""
    db_path = tmp_path / "raids_test.db"
    with patch("backend.server.api.raid_strategies.raids_db", raids_db.RaidCatalogue(db_path)):
        yield db_path


# ---------------------------------------------------------------------------
# Tests that exercise the real DB through _write_overview_sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_overview_write_creates_revision_with_null_before(app, raids_tmp):
    """PUT a brand-new overview → GET revisions shows one row with before_md=None."""
    raids_db.RaidCatalogue(raids_tmp).init_db().close()

    with patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()):
        async with _writer_client(app) as client:
            put_r = await client.put(
                "/api/zones/The Emerald Halls/overview",
                json={"markdown": "## Opening tactics"},
            )
            assert put_r.status_code == 200

            get_r = await client.get("/api/zones/The Emerald Halls/overview/revisions")
    assert get_r.status_code == 200
    data = get_r.json()
    assert data["zone_name"] == "The Emerald Halls"
    revs = data["revisions"]
    assert len(revs) == 1
    assert revs[0]["before_md"] is None
    assert revs[0]["after_md"] == "## Opening tactics"
    assert revs[0]["edited_by"] == "admin-1"


@pytest.mark.asyncio
async def test_overview_update_creates_revision_with_before_and_after(app, raids_tmp):
    """PUT twice with different markdown → two revision rows newest-first."""
    raids_db.RaidCatalogue(raids_tmp).init_db().close()

    with patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()):
        async with _writer_client(app) as client:
            await client.put(
                "/api/zones/The Emerald Halls/overview",
                json={"markdown": "first version"},
            )
            await client.put(
                "/api/zones/The Emerald Halls/overview",
                json={"markdown": "second version", "edit_note": "improved wording"},
            )
            get_r = await client.get("/api/zones/The Emerald Halls/overview/revisions")

    assert get_r.status_code == 200
    revs = get_r.json()["revisions"]
    assert len(revs) == 2
    # Newest first
    assert revs[0]["after_md"] == "second version"
    assert revs[0]["before_md"] == "first version"
    assert revs[0]["edit_note"] == "improved wording"
    # Oldest
    assert revs[1]["after_md"] == "first version"
    assert revs[1]["before_md"] is None


@pytest.mark.asyncio
async def test_overview_unchanged_skips_revision(app, raids_tmp):
    """PUT the same markdown twice → only ONE revision row (no duplicate)."""
    raids_db.RaidCatalogue(raids_tmp).init_db().close()

    same_md = "## tactics unchanged"
    with patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()):
        async with _writer_client(app) as client:
            await client.put("/api/zones/The Emerald Halls/overview", json={"markdown": same_md})
            await client.put("/api/zones/The Emerald Halls/overview", json={"markdown": same_md})
            get_r = await client.get("/api/zones/The Emerald Halls/overview/revisions")

    assert get_r.status_code == 200
    revs = get_r.json()["revisions"]
    assert len(revs) == 1


@pytest.mark.asyncio
async def test_revisions_endpoint_404_unknown_zone(app):
    """GET revisions for a zone that zones_db doesn't know → 404."""
    with patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/NoSuchZone/overview/revisions")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_revisions_endpoint_returns_empty_when_no_overview_written(app, raids_tmp):
    """Zone exists in zones.db but no overview PUT yet → 200 with empty revisions list."""
    raids_db.RaidCatalogue(raids_tmp).init_db().close()

    with patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls/overview/revisions")

    assert r.status_code == 200
    assert r.json()["revisions"] == []


@pytest.mark.asyncio
async def test_revisions_endpoint_returns_editor_display_name(app, raids_tmp):
    """PUT overview as a known user → GET revisions shows edited_by_name."""
    raids_db.RaidCatalogue(raids_tmp).init_db().close()

    # Seed the user so get_display_names_for_discord_ids resolves.
    await users_db.upsert_user(
        discord_id="known-editor-1",
        discord_name="Velious Raider",
        discord_username="vraider",
        avatar=None,
    )

    from backend.server.auth_deps import require_editor

    app.dependency_overrides[require_editor] = lambda: {"id": "known-editor-1", "username": "vraider"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()):
            await client.put(
                "/api/zones/The Emerald Halls/overview",
                json={"markdown": "## Edited by known user"},
            )
            get_r = await client.get("/api/zones/The Emerald Halls/overview/revisions")

    assert get_r.status_code == 200
    revs = get_r.json()["revisions"]
    assert len(revs) == 1
    assert revs[0]["edited_by"] == "known-editor-1"
    assert revs[0]["edited_by_name"] == "Velious Raider"
