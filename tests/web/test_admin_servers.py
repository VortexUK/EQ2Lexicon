"""Tests for per-server admin scoping + the server-settings editor endpoints.

Covers:
- GET  /api/admin/servers  → lists servers with their settings (admin-only)
- PUT  /api/admin/servers/{world}  → updates settings + refreshes in-memory
  registry (admin-only); 404 for unknown world; 422 for bad max_level
- Admin claims list  → scoped to current_world() (x-server override)
- Admin parses list  → scoped to current_world() (x-server override)
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Shared admin-auth stub (mirrors test_admin_roles.py convention)
# ---------------------------------------------------------------------------


def _fake_admin(request=None):
    return {"id": "admin1", "username": "boss"}


# ---------------------------------------------------------------------------
# GET /api/admin/servers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_servers_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/admin/servers")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_list_servers_returns_all_servers(app):
    servers = [
        {
            "world": "Varsoon",
            "subdomain": "varsoon",
            "display_name": "Varsoon",
            "max_level": 60,
            "current_xpac": "Desert of Flames",
            "launch_dt": "2023-05-24",
        },
        {
            "world": "Wuoshi",
            "subdomain": "wuoshi",
            "display_name": "Wuoshi",
            "max_level": 50,
            "current_xpac": None,
            "launch_dt": None,
        },
    ]
    with (
        patch("web.routes.admin._require_admin", _fake_admin),
        patch("web.routes.admin.list_servers_sync", return_value=servers),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/admin/servers")

    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    worlds = {s["world"] for s in data}
    assert worlds == {"Varsoon", "Wuoshi"}
    # Settings fields present
    wuoshi = next(s for s in data if s["world"] == "Wuoshi")
    assert wuoshi["max_level"] == 50
    assert wuoshi["current_xpac"] is None


# ---------------------------------------------------------------------------
# PUT /api/admin/servers/{world}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_servers_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.put("/api/admin/servers/Wuoshi", json={"max_level": 70})
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_put_servers_unknown_world_is_404(app):
    with (
        patch("web.routes.admin._require_admin", _fake_admin),
        patch("web.routes.admin.list_servers_sync", return_value=[]),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put("/api/admin/servers/NoSuchWorld", json={"max_level": 50})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_put_servers_bad_max_level_is_422(app):
    servers = [
        {
            "world": "Wuoshi",
            "subdomain": "wuoshi",
            "display_name": "Wuoshi",
            "max_level": 50,
            "current_xpac": None,
            "launch_dt": None,
        }
    ]
    with (
        patch("web.routes.admin._require_admin", _fake_admin),
        patch("web.routes.admin.list_servers_sync", return_value=servers),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put("/api/admin/servers/Wuoshi", json={"max_level": 0})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_put_servers_updates_settings_and_refreshes_registry(app):
    """PUT upserts the row, refreshes the registry, returns the updated server."""
    servers = [
        {
            "world": "Wuoshi",
            "subdomain": "wuoshi",
            "display_name": "Wuoshi",
            "max_level": 50,
            "current_xpac": None,
            "launch_dt": None,
        },
    ]
    updated_server = {
        "world": "Wuoshi",
        "subdomain": "wuoshi",
        "display_name": "Wuoshi",
        "max_level": 70,
        "current_xpac": "Sentinel's Fate",
        "launch_dt": None,
    }
    mock_upsert = MagicMock()
    mock_reload = MagicMock()

    with (
        patch("web.routes.admin._require_admin", _fake_admin),
        patch("web.routes.admin.list_servers_sync", return_value=servers),
        patch("web.routes.admin.upsert_server_settings_sync", mock_upsert),
        patch("web.routes.admin.server_context.load_registry", mock_reload),
        patch("web.routes.admin.get_server_by_world_sync", return_value=updated_server),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/admin/servers/Wuoshi",
                json={"max_level": 70, "current_xpac": "Sentinel's Fate", "launch_dt": None},
            )

    assert r.status_code == 200
    body = r.json()
    assert body["world"] == "Wuoshi"
    assert body["max_level"] == 70
    assert body["current_xpac"] == "Sentinel's Fate"

    # Verify the DB helper was called with the right args
    mock_upsert.assert_called_once_with(
        "Wuoshi",
        max_level=70,
        current_xpac="Sentinel's Fate",
        launch_dt=None,
    )
    # Registry MUST be refreshed after writing
    mock_reload.assert_called_once()


# ---------------------------------------------------------------------------
# Admin claims list — scoped to current_world()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_claims_scoped_to_current_world(app):
    """Admin seeing claims should only see claims for the active server world."""
    wuoshi_claim = {
        "id": 10,
        "discord_id": "u1",
        "discord_name": "Alice",
        "discord_username": "alice",
        "avatar": None,
        "character_name": "Alicewuoshi",
        "status": "pending",
        "requested_at": 100,
        "reviewed_at": None,
        "reviewed_by": None,
        "note": None,
        "world": "Wuoshi",
    }
    varsoon_claim = {
        "id": 11,
        "discord_id": "u2",
        "discord_name": "Bob",
        "discord_username": "bob",
        "avatar": None,
        "character_name": "Bobvarsoon",
        "status": "pending",
        "requested_at": 90,
        "reviewed_at": None,
        "reviewed_by": None,
        "note": None,
        "world": "Varsoon",
    }

    captured_world: list[str] = []

    async def _mock_list_claims(*, status=None, world=None):
        captured_world.append(world or "")
        return [wuoshi_claim] if world == "Wuoshi" else [varsoon_claim]

    # Patch current_world() to return Wuoshi — simulates the middleware resolving
    # the request to the Wuoshi subdomain.
    with (
        patch("web.routes.admin._require_admin", _fake_admin),
        patch("web.routes.admin.list_claims", _mock_list_claims),
        patch("web.routes.admin.current_world", return_value="Wuoshi"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/admin/claims")

    assert r.status_code == 200
    data = r.json()
    # Only the Wuoshi claim should be returned
    assert len(data) == 1
    assert data[0]["character_name"] == "Alicewuoshi"
    # Confirm world was passed to list_claims
    assert captured_world == ["Wuoshi"]


# ---------------------------------------------------------------------------
# Admin parses list — scoped to current_world()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_parses_scoped_to_current_world(app):
    """Admin parses view should only show encounters for the active server."""
    wuoshi_row = {
        "id": 1,
        "title": "Wuoshi Encounter",
        "zone": "Antonica",
        "guild_name": "Exordium",
        "uploaded_by": "Alice",
        "started_at": 100,
        "duration_s": 60,
        "success_level": 1,
        "hidden_at": None,
        "player_count": 5,
    }
    varsoon_row = {
        "id": 2,
        "title": "Varsoon Encounter",
        "zone": "Commonlands",
        "guild_name": "Exordium",
        "uploaded_by": "Bob",
        "started_at": 90,
        "duration_s": 30,
        "success_level": 1,
        "hidden_at": None,
        "player_count": 3,
    }

    def _mock_admin_list(conn, *, search=None, limit=200, world=None):
        return [wuoshi_row] if world == "Wuoshi" else [varsoon_row]

    # Patch current_world() to simulate a Wuoshi-scoped request.
    with (
        patch("web.routes.admin._require_admin", _fake_admin),
        patch("web.routes.admin.current_world", return_value="Wuoshi"),
        patch("web.routes.admin.parses_db.list_encounters_for_admin", _mock_admin_list),
        patch("web.routes.admin.parses_db.init_db", MagicMock(return_value=MagicMock())),
        patch("web.routes.admin.parses_db.DB_PATH") as mock_path,
    ):
        mock_path.exists.return_value = True
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/admin/parses")

    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["title"] == "Wuoshi Encounter"
