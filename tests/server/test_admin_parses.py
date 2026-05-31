"""Tests for the admin parses-sanitize endpoint — auth gate and the
hidden_at → hidden flag mapping."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
        patch("backend.server.api.admin.parses_db.DB_PATH") as mock_path,
    ):
        mock_path.exists.return_value = True
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/admin/parses")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["id"] == 1 and body[0]["hidden"] is True
    assert body[1]["hidden"] is False
