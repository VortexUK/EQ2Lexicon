"""Tests for the self-service role-request flow — both user-side
(/api/me/role-requests) and admin-side review (/api/admin/role-requests)."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.users import make_fake_admin

_fake_admin_user = make_fake_admin()


def _fake_admin(request=None):
    return _fake_admin_user


def _fake_session_user(request=None):
    return {"id": "user-42", "username": "alice"}


def _fake_request_row(
    *,
    request_id: int = 1,
    discord_id: str = "user-42",
    role: str = "contributor",
    status: str = "pending",
    user_note: str | None = None,
    admin_note: str | None = None,
    reviewed_by: str | None = None,
) -> dict:
    return {
        "id": request_id,
        "discord_id": discord_id,
        "discord_name": "Alice",
        "discord_username": "alice",
        "avatar": None,
        "role": role,
        "status": status,
        "requested_at": 1716000000,
        "reviewed_at": 1716100000 if status != "pending" else None,
        "reviewed_by": reviewed_by,
        "user_note": user_note,
        "admin_note": admin_note,
    }


# ---------------------------------------------------------------------------
# User-side: POST /api/me/role-requests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_role_request_requires_session(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/me/role-requests", json={"role": "contributor"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_submit_role_request_rejects_unknown_role(app):
    with patch("backend.server.api.role_requests.require_user_session", _fake_session_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/me/role-requests", json={"role": "wizard"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_submit_role_request_rejects_when_user_already_has_role(app):
    """No point queueing a grant the user already holds — fail fast with 409."""
    with (
        patch("backend.server.api.role_requests.require_user_session", _fake_session_user),
        patch("backend.server.api.role_requests.has_role", return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/me/role-requests", json={"role": "contributor"})
    assert r.status_code == 409
    assert "already have" in r.json()["detail"]


@pytest.mark.asyncio
async def test_submit_role_request_409_when_pending_exists(app):
    """The partial unique index catches double-submits — route surfaces it
    as a clean 409 rather than a 500."""
    with (
        patch("backend.server.api.role_requests.require_user_session", _fake_session_user),
        patch("backend.server.api.role_requests.has_role", return_value=False),
        patch(
            "backend.server.api.role_requests.create_role_request",
            side_effect=sqlite3.IntegrityError("UNIQUE constraint failed"),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/me/role-requests", json={"role": "contributor"})
    assert r.status_code == 409
    assert "pending request" in r.json()["detail"]


@pytest.mark.asyncio
async def test_submit_role_request_writes_and_returns_row(app):
    fresh = _fake_request_row(request_id=7, user_note="i want to help")
    with (
        patch("backend.server.api.role_requests.require_user_session", _fake_session_user),
        patch("backend.server.api.role_requests.has_role", return_value=False),
        patch("backend.server.api.role_requests.create_role_request", return_value=7) as m_create,
        patch("backend.server.api.role_requests.list_role_requests", return_value=[fresh]),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/me/role-requests",
                json={"role": "contributor", "note": "i want to help"},
            )

    assert r.status_code == 201
    data = r.json()
    assert data["id"] == 7
    assert data["status"] == "pending"
    assert data["user_note"] == "i want to help"
    m_create.assert_awaited_once_with("user-42", "contributor", "i want to help")


# ---------------------------------------------------------------------------
# User-side: GET /api/me/role-requests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_my_role_requests_returns_only_my_history(app):
    """Helper is called with the session user's id — proves cross-user
    leakage isn't possible from the route layer."""
    history = [
        _fake_request_row(request_id=3, status="approved", reviewed_by="admin-1"),
        _fake_request_row(request_id=1, status="rejected", admin_note="not yet"),
    ]
    with (
        patch("backend.server.api.role_requests.require_user_session", _fake_session_user),
        patch(
            "backend.server.api.role_requests.list_role_requests",
            new_callable=AsyncMock,
            return_value=history,
        ) as m_list,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/me/role-requests")

    assert r.status_code == 200
    assert [row["id"] for row in r.json()] == [3, 1]
    m_list.assert_awaited_once_with(discord_id="user-42")


# ---------------------------------------------------------------------------
# User-side: DELETE /api/me/role-requests/{id} (withdraw)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_withdraw_request_404_when_not_owned_or_resolved(app):
    """Helper is scoped to (id, discord_id, status='pending') — returning
    False for any of those misses surfaces as 404."""
    with (
        patch("backend.server.api.role_requests.require_user_session", _fake_session_user),
        patch("backend.server.api.role_requests.withdraw_role_request", return_value=False),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/me/role-requests/99")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_withdraw_request_happy_path(app):
    with (
        patch("backend.server.api.role_requests.require_user_session", _fake_session_user),
        patch("backend.server.api.role_requests.withdraw_role_request", return_value=True) as m,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/me/role-requests/7")
    assert r.status_code == 200
    m.assert_awaited_once_with(7, "user-42")


# ---------------------------------------------------------------------------
# Admin-side: GET /api/admin/role-requests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_list_role_requests_defaults_to_pending(app):
    queue = [_fake_request_row(request_id=2), _fake_request_row(request_id=5)]
    with (
        patch("backend.server.api.admin._require_admin", _fake_admin),
        patch("backend.server.api.admin.list_role_requests", new_callable=AsyncMock, return_value=queue) as m,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/admin/role-requests")

    assert r.status_code == 200
    assert [row["id"] for row in r.json()] == [2, 5]
    m.assert_awaited_once_with(status="pending")


@pytest.mark.asyncio
async def test_admin_list_role_requests_respects_status_filter(app):
    with (
        patch("backend.server.api.admin._require_admin", _fake_admin),
        patch("backend.server.api.admin.list_role_requests", new_callable=AsyncMock, return_value=[]) as m,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/admin/role-requests?status=approved")
    assert r.status_code == 200
    m.assert_awaited_once_with(status="approved")


# ---------------------------------------------------------------------------
# Admin-side: POST /api/admin/role-requests/{id}/approve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_request_404_when_unknown(app):
    with (
        patch("backend.server.api.admin._require_admin", _fake_admin),
        patch("backend.server.api.admin.get_role_request", return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/admin/role-requests/999/approve", json={})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_approve_request_409_when_already_resolved(app):
    """Can't re-approve a request that's already been rejected/approved/
    withdrawn — keeps the audit trail single-pass."""
    resolved = _fake_request_row(status="approved")
    with (
        patch("backend.server.api.admin._require_admin", _fake_admin),
        patch("backend.server.api.admin.get_role_request", return_value=resolved),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/admin/role-requests/1/approve", json={})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_approve_request_grants_role_after_marking_approved(app):
    """Happy path: review_and_grant_role atomically flips the row and grants
    the role in a single transaction. The admin id is stamped through."""
    pending = _fake_request_row(status="pending")
    reviewed = _fake_request_row(status="approved", reviewed_by="admin-1", admin_note="welcome!")
    with (
        patch("backend.server.api.admin._require_admin", _fake_admin),
        patch("backend.server.api.admin.get_role_request", return_value=pending),
        patch(
            "backend.server.api.admin.review_and_grant_role",
            new_callable=AsyncMock,
            return_value=reviewed,
        ) as m_review_and_grant,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/admin/role-requests/1/approve",
                json={"note": "welcome!"},
            )

    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    m_review_and_grant.assert_awaited_once_with(1, "approved", "admin-1", "welcome!")


@pytest.mark.asyncio
async def test_approve_request_409_on_lost_race(app):
    """review_and_grant_role returns None when another admin already moved the
    row out of pending between get + update — surface as 409."""
    pending = _fake_request_row(status="pending")
    with (
        patch("backend.server.api.admin._require_admin", _fake_admin),
        patch("backend.server.api.admin.get_role_request", return_value=pending),
        patch(
            "backend.server.api.admin.review_and_grant_role",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/admin/role-requests/1/approve", json={})

    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Admin-side: POST /api/admin/role-requests/{id}/reject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_request_writes_admin_note(app):
    pending = _fake_request_row(status="pending")
    rejected = _fake_request_row(status="rejected", reviewed_by="admin-1", admin_note="not now")
    with (
        patch("backend.server.api.admin._require_admin", _fake_admin),
        patch("backend.server.api.admin.get_role_request", return_value=pending),
        patch("backend.server.api.admin.review_role_request", return_value=rejected) as m,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/admin/role-requests/1/reject",
                json={"note": "not now"},
            )

    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["admin_note"] == "not now"
    m.assert_awaited_once_with(1, "rejected", "admin-1", "not now")
