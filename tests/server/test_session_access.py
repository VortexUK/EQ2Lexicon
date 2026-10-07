"""The session-cookie access gate (backend/server/core/session_access.py).

A signed cookie used to be a login for 14 days no matter what an admin did
to the account. These tests pin: pending/denied accounts reach only
/api/auth/*, a kicked or denied account's live cookie dies (epoch bump), an
erased account's cookie dies (no row), admins skip the status check but not
the epoch check, and the admin routes invalidate the per-user cache.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.core import session_access
from backend.server.db import bump_session_epoch, get_session_access, set_user_access, upsert_user

_TEST_SECRET = "pytest-session-secret-not-real-0123456789"

pytestmark = pytest.mark.usefixtures("users_schema", "session_access_enforced")


def _cookie(user_id: str, epoch: int = 0) -> dict:
    import itsdangerous

    payload = json.dumps({"user": {"id": user_id, "username": user_id, "epoch": epoch}})
    signed = itsdangerous.TimestampSigner(_TEST_SECRET).sign(base64.b64encode(payload.encode()).decode())
    return {"session": signed.decode()}


async def _seed(user_id: str, status: str) -> None:
    await upsert_user(discord_id=user_id, discord_name=user_id, discord_username=user_id, avatar=None)
    assert await set_user_access(user_id, status)
    session_access.invalidate(user_id)


def _client(app, cookies: dict) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies)


# -- unit: check_session ------------------------------------------------------


@pytest.mark.asyncio
async def test_check_session_verdicts():
    await _seed("sa-approved", "approved")
    await _seed("sa-pending", "pending")
    await _seed("sa-denied", "denied")

    assert await session_access.check_session({"id": "sa-approved", "epoch": 0}, "/api/guild/X") == "ok"
    assert await session_access.check_session({"id": "sa-approved"}, "/api/guild/X") == "ok"  # pre-epoch cookie
    assert await session_access.check_session({"id": "sa-approved", "epoch": 3}, "/api/guild/X") == "revoked"
    assert await session_access.check_session({"id": "sa-pending", "epoch": 0}, "/api/guild/X") == "not_approved"
    assert await session_access.check_session({"id": "sa-pending", "epoch": 0}, "/api/auth/me") == "ok"
    assert await session_access.check_session({"id": "sa-denied", "epoch": 0}, "/api/parses") == "not_approved"
    assert await session_access.check_session({"id": "sa-ghost", "epoch": 0}, "/api/auth/me") == "revoked"


@pytest.mark.asyncio
async def test_admin_skips_status_but_not_epoch():
    await _seed("sa-admin", "denied")
    with patch.object(session_access, "ADMIN_IDS", frozenset({"sa-admin"})):
        assert await session_access.check_session({"id": "sa-admin", "epoch": 0}, "/api/admin/users") == "ok"
        assert await session_access.check_session({"id": "sa-admin", "epoch": 9}, "/api/admin/users") == "revoked"


@pytest.mark.asyncio
async def test_bump_epoch_and_cache_invalidation():
    await _seed("sa-bump", "approved")
    assert await session_access.check_session({"id": "sa-bump", "epoch": 0}, "/x") == "ok"
    new_epoch = await bump_session_epoch("sa-bump")
    assert new_epoch == 1
    # Cached verdict still says ok until invalidated — the admin routes call invalidate().
    assert await session_access.check_session({"id": "sa-bump", "epoch": 0}, "/x") == "ok"
    session_access.invalidate("sa-bump")
    assert await session_access.check_session({"id": "sa-bump", "epoch": 0}, "/x") == "revoked"
    assert await session_access.check_session({"id": "sa-bump", "epoch": 1}, "/x") == "ok"
    assert await get_session_access("sa-bump") == ("approved", 1)


# -- HTTP: middleware behaviour ------------------------------------------------


@pytest.mark.asyncio
async def test_pending_user_sees_me_but_nothing_else(app):
    await _seed("sa-http-pending", "pending")
    async with _client(app, _cookie("sa-http-pending")) as client:
        me = await client.get("/api/auth/me")
        tokens = await client.get("/api/auth/tokens")
        other = await client.get("/api/aa/plans?character=Sihtric")
    assert me.status_code == 200 and me.json()["access_status"] == "pending"
    assert tokens.status_code == 200  # /api/auth/* stays reachable
    assert other.status_code == 401


@pytest.mark.asyncio
async def test_approved_user_passes(app):
    await _seed("sa-http-ok", "approved")
    async with _client(app, _cookie("sa-http-ok")) as client:
        r = await client.get("/api/auth/tokens")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_revoked_cookie_is_logged_out_everywhere(app):
    await _seed("sa-http-revoked", "approved")
    await bump_session_epoch("sa-http-revoked")
    session_access.invalidate("sa-http-revoked")
    async with _client(app, _cookie("sa-http-revoked", epoch=0)) as client:
        r = await client.get("/api/auth/me")
    assert r.status_code == 401
    # SessionMiddleware clears the now-empty session cookie.
    set_cookie = r.headers.get("set-cookie", "").lower()
    assert "session=" in set_cookie and ("max-age=0" in set_cookie or "expires=" in set_cookie)


@pytest.mark.asyncio
async def test_missing_users_row_is_logged_out(app):
    async with _client(app, _cookie("sa-http-ghost")) as client:
        r = await client.get("/api/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_admin_deny_kills_live_session(app):
    await _seed("sa-http-victim", "approved")
    await _seed("sa-http-admin", "approved")
    with (
        patch.object(session_access, "ADMIN_IDS", frozenset({"sa-http-admin"})),
        patch("backend.server.auth_deps.ADMIN_IDS", frozenset({"sa-http-admin"})),
    ):
        async with _client(app, _cookie("sa-http-victim")) as victim:
            assert (await victim.get("/api/auth/tokens")).status_code == 200
        async with _client(app, _cookie("sa-http-admin")) as admin:
            r = await admin.post("/api/admin/users/sa-http-victim/deny")
            assert r.status_code == 200, r.text
        async with _client(app, _cookie("sa-http-victim")) as victim:
            assert (await victim.get("/api/auth/me")).status_code == 401
