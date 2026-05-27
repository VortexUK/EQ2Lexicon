"""Tests for the admin role-management endpoints + the roles join on the
user listing."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


def _fake_admin(request=None):
    return {"id": "admin-1", "username": "boss"}


# ---------------------------------------------------------------------------
# GET /api/admin/users — now includes roles[]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_users_includes_roles(app):
    """Joined-in roles per user, no N+1 over `list_roles_for_user`."""
    users = [
        {
            "discord_id": "u1",
            "discord_name": "Alice",
            "discord_username": "alice",
            "avatar": None,
            "first_seen": 1,
            "last_seen": 2,
            "access_status": "approved",
            "claim_count": 0,
        },
        {
            "discord_id": "u2",
            "discord_name": "Bob",
            "discord_username": "bob",
            "avatar": None,
            "first_seen": 3,
            "last_seen": 4,
            "access_status": "approved",
            "claim_count": 1,
        },
    ]
    role_map = {"u2": ["contributor"]}  # u1 has none

    with (
        patch("web.routes.admin._require_admin", _fake_admin),
        patch("web.routes.admin.list_all_users", return_value=users),
        patch("web.routes.admin.list_role_assignments", return_value=role_map),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/admin/users")

    assert r.status_code == 200
    data = r.json()
    by_id = {u["discord_id"]: u for u in data}
    assert by_id["u1"]["roles"] == []
    assert by_id["u2"]["roles"] == ["contributor"]


# ---------------------------------------------------------------------------
# POST /api/admin/users/{discord_id}/roles/{role}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grant_role_requires_admin(app):
    """Unauthenticated → 401/403 from the real require_admin."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/admin/users/u9/roles/contributor")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_grant_role_unknown_role_is_400(app):
    """Typo guard — unknown role rejected before touching the DB."""
    with patch("web.routes.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/admin/users/u1/roles/contibutor")  # typo
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_grant_role_writes_with_admin_as_grantor(app):
    """Successful grant calls the DB helper with the admin's id stamped as
    granted_by."""
    with (
        patch("web.routes.admin._require_admin", _fake_admin),
        patch("web.routes.admin.grant_role", return_value=True) as m,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/admin/users/u1/roles/contributor")

    assert r.status_code == 200
    assert r.json() == {"ok": True, "granted": True}
    m.assert_awaited_once_with("u1", "contributor", "admin-1")


@pytest.mark.asyncio
async def test_grant_role_idempotent(app):
    """Re-granting an existing (user, role) row returns ok=True, granted=False.
    Lets the UI not worry about double-clicks."""
    with (
        patch("web.routes.admin._require_admin", _fake_admin),
        patch("web.routes.admin.grant_role", return_value=False),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/admin/users/u1/roles/contributor")

    assert r.status_code == 200
    assert r.json() == {"ok": True, "granted": False}


# ---------------------------------------------------------------------------
# DELETE /api/admin/users/{discord_id}/roles/{role}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_role_requires_admin(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/admin/users/u9/roles/contributor")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_revoke_role_unknown_role_is_400(app):
    with patch("web.routes.admin._require_admin", _fake_admin):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/admin/users/u1/roles/moderator")  # not in KNOWN_ROLES yet
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_revoke_role_user_didnt_have_it_is_404(app):
    """DB helper returns False (no row deleted) → 404."""
    with (
        patch("web.routes.admin._require_admin", _fake_admin),
        patch("web.routes.admin.revoke_role", return_value=False),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/admin/users/u1/roles/contributor")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_revoke_role_happy_path(app):
    with (
        patch("web.routes.admin._require_admin", _fake_admin),
        patch("web.routes.admin.revoke_role", return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/admin/users/u1/roles/contributor")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
