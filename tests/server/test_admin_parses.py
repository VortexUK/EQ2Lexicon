"""Tests for the admin parses-sanitize endpoint — auth gate and the
hidden_at → hidden flag mapping."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.users import make_fake_admin

_fake_admin_user = make_fake_admin(id="admin1")


def _fake_admin(request=None):
    return _fake_admin_user


@pytest.mark.asyncio
async def test_admin_parses_requires_admin(app):
    # Real require_admin raises 401/403 for a non-admin / unauthenticated request.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/admin/parses")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_admin_parses_lists_including_hidden(app):
    rows = [
        {
            "id": 1,
            "title": "Wuoshi",
            "zone": "The Emerald Halls",
            "guild_name": "Exordium",
            "uploaded_by": "Menludiir",
            "started_at": 100,
            "duration_s": 60,
            "success_level": 1,
            "hidden_at": 99,
            "hidden_by": "officer-7",
            "player_count": 24,
        },
        {
            "id": 2,
            "title": "a krait patriarch",
            "zone": "Z",
            "guild_name": "Exordium",
            "uploaded_by": "Menludiir",
            "started_at": 90,
            "duration_s": 30,
            "success_level": 1,
            "hidden_by": None,
            "hidden_at": None,
            "player_count": 1,
        },
    ]
    with (
        patch("backend.server.api.admin._require_admin", _fake_admin),
        patch(
            "backend.server.api.admin.parses_db.list_encounters_for_admin",
            MagicMock(return_value=rows),
        ),
        patch("backend.server.api.admin.parses_db.init_db", MagicMock(return_value=MagicMock())),
        patch(
            "backend.server.api.admin.get_display_names_for_discord_ids",
            AsyncMock(return_value={"officer-7": "Vortex"}),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/admin/parses")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["id"] == 1 and body[0]["hidden"] is True
    assert body[0]["hidden_at"] == 99
    assert body[0]["hidden_by"] == "officer-7"
    assert body[0]["hidden_by_name"] == "Vortex"
    assert body[1]["hidden"] is False
    assert body[1]["hidden_by"] is None and body[1]["hidden_by_name"] is None


@pytest.mark.asyncio
async def test_admin_parses_hidden_filter_reaches_the_store(app):
    """``?hidden=true`` narrows the list to soft-deleted rows — the bulk
    restore workflow starts here."""
    list_mock = MagicMock(return_value=[])
    with (
        patch("backend.server.api.admin._require_admin", _fake_admin),
        patch("backend.server.api.admin.parses_db.list_encounters_for_admin", list_mock),
        patch("backend.server.api.admin.parses_db.init_db", MagicMock(return_value=MagicMock())),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/admin/parses?hidden=true&search=exordium")
    assert r.status_code == 200 and r.json() == []
    assert list_mock.call_args.kwargs["hidden_only"] is True
    assert list_mock.call_args.kwargs["search"] == "exordium"


@pytest.mark.asyncio
async def test_admin_parses_column_filters_reach_the_store(app):
    """Each column's filter is its own query parameter and reaches the store
    unchanged, so the table can combine zone AND uploader AND result."""
    list_mock = MagicMock(return_value=[])
    with (
        patch("backend.server.api.admin._require_admin", _fake_admin),
        patch("backend.server.api.admin.parses_db.list_encounters_for_admin", list_mock),
        patch("backend.server.api.admin.parses_db.init_db", MagicMock(return_value=MagicMock())),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/admin/parses?status=visible&id=7&title=corn&zone=thundering&guild=exo"
                "&uploader=men&since=100&until=200&min_players=24&result=1&limit=1000"
            )
            bad = await client.get("/api/admin/parses?result=9")
    assert r.status_code == 200
    kw = list_mock.call_args_list[0].kwargs
    assert kw["visible_only"] is True and kw["hidden_only"] is False
    assert (kw["encounter_id"], kw["title"], kw["zone"], kw["guild"], kw["uploader"]) == (
        7,
        "corn",
        "thundering",
        "exo",
        "men",
    )
    assert (kw["since"], kw["until"], kw["min_players"], kw["result"], kw["limit"]) == (100, 200, 24, 1, 1000)
    assert bad.status_code == 422
