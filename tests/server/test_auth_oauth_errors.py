"""OAuth callback error-path tests for web/routes/auth.py — COV-033.

Covers the branches not exercised by test_auth.py's happy-path flow:
  callback state mismatch         → 400
  callback token exchange failure → 400
  callback user-info fetch failure → 400
  GET /auth/me for admin user      → is_admin=True, access_status forced "approved"
  POST /auth/logout without session → 200 ok (graceful no-op)
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.app import create_app

_SECRET = "pytest-session-secret-not-real-0123456789"


@pytest.fixture
def app():
    """App fixture with a pinned session secret (matches test_auth.py pattern)."""
    return create_app(session_secret=_SECRET)


def _make_session_cookie(user: dict) -> str:
    """Build a signed itsdangerous session cookie with the given user dict."""
    import itsdangerous

    payload = json.dumps({"user": user})
    encoded = base64.b64encode(payload.encode()).decode()
    signer = itsdangerous.TimestampSigner(_SECRET)
    return signer.sign(encoded).decode()


# ---------------------------------------------------------------------------
# Callback — state mismatch
# ---------------------------------------------------------------------------


class TestCallbackStateMismatch:
    async def test_missing_state_in_session_returns_400(self, app) -> None:
        """Callback with no state in session raises 400 (Invalid OAuth state)."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            r = await client.get("/api/auth/callback?code=fake&state=something")

        assert r.status_code == 400
        assert "state" in r.json()["detail"].lower()

    async def test_state_mismatch_returns_400(self, app) -> None:
        """Callback with a state that doesn't match the session raises 400."""
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            # Trigger login to plant a real state in the session
            login_r = await client.get("/api/auth/login")
            assert login_r.status_code in (302, 307)
            # Pass back a WRONG state value
            r = await client.get("/api/auth/callback?code=fake&state=WRONG_STATE")

        assert r.status_code == 400
        assert "state" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Callback — Discord API errors
# ---------------------------------------------------------------------------


class TestCallbackDiscordErrors:
    async def test_token_exchange_failure_returns_400(self, app) -> None:
        """When Discord returns a non-200 on token exchange, a 400 is returned."""
        mock_token = MagicMock()
        mock_token.status_code = 401
        mock_token.text = "Unauthorized"

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_token)

        with patch("backend.server.api.auth.httpx.AsyncClient") as MockHttpx:
            MockHttpx.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockHttpx.return_value.__aexit__ = AsyncMock(return_value=False)

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                # Plant state in the session first
                login_r = await client.get("/api/auth/login")
                from urllib.parse import parse_qs, urlparse

                state = parse_qs(urlparse(login_r.headers["location"]).query)["state"][0]
                r = await client.get(f"/api/auth/callback?code=fake&state={state}")

        assert r.status_code == 400
        assert "oauth code" in r.json()["detail"].lower()

    async def test_user_info_fetch_failure_returns_400(self, app) -> None:
        """When Discord returns a non-200 on the user-info call, a 400 is returned."""
        mock_token = MagicMock()
        mock_token.status_code = 200
        mock_token.json.return_value = {"access_token": "valid-token"}

        mock_user_resp = MagicMock()
        mock_user_resp.status_code = 403
        mock_user_resp.text = "Forbidden"

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_token)
        mock_http.get = AsyncMock(return_value=mock_user_resp)

        with patch("backend.server.api.auth.httpx.AsyncClient") as MockHttpx:
            MockHttpx.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockHttpx.return_value.__aexit__ = AsyncMock(return_value=False)

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as client:
                login_r = await client.get("/api/auth/login")
                from urllib.parse import parse_qs, urlparse

                state = parse_qs(urlparse(login_r.headers["location"]).query)["state"][0]
                r = await client.get(f"/api/auth/callback?code=fake&state={state}")

        assert r.status_code == 400
        assert "discord user" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /auth/me — admin user
# ---------------------------------------------------------------------------


class TestMeAdmin:
    async def test_admin_user_has_is_admin_true_and_forced_approved(self, app) -> None:
        """Admin IDs always return is_admin=True and access_status=approved,
        regardless of what the DB would return for access_status."""
        admin_id = "admin-1"
        fake_user = {
            "id": admin_id,
            "username": "boss",
            "global_name": "The Boss",
            "avatar": None,
        }
        cookie = _make_session_cookie(fake_user)

        with (
            patch("backend.server.api.auth._ADMIN_IDS", {admin_id}),
            patch(
                "backend.server.api.auth.list_roles_for_user",
                AsyncMock(return_value=[]),
            ),
            # get_user_access_status should NOT be called for admins
            patch(
                "backend.server.api.auth.get_user_access_status",
                AsyncMock(side_effect=AssertionError("should not call for admin")),
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                client.cookies.set("session", cookie)
                r = await client.get("/api/auth/me")

        assert r.status_code == 200
        body = r.json()
        assert body["is_admin"] is True
        assert body["access_status"] == "approved"

    async def test_non_admin_user_has_is_admin_false(self, app) -> None:
        """A regular user's is_admin should be False."""
        fake_user = {
            "id": "user-99",
            "username": "normaluser",
            "global_name": None,
            "avatar": None,
        }
        cookie = _make_session_cookie(fake_user)

        with (
            patch("backend.server.api.auth._ADMIN_IDS", set()),
            patch(
                "backend.server.api.auth.list_roles_for_user",
                AsyncMock(return_value=[]),
            ),
            patch(
                "backend.server.api.auth.get_user_access_status",
                AsyncMock(return_value="approved"),
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                client.cookies.set("session", cookie)
                r = await client.get("/api/auth/me")

        assert r.status_code == 200
        assert r.json()["is_admin"] is False


# ---------------------------------------------------------------------------
# POST /auth/logout — no session
# ---------------------------------------------------------------------------


class TestLogoutEdgeCases:
    async def test_logout_without_session_still_returns_ok(self, app) -> None:
        """Logout with no active session is a graceful no-op returning ok."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/auth/logout")

        assert r.status_code == 200
        assert r.json()["ok"] is True

    async def test_logout_with_session_audit_logs_user_id(self, app) -> None:
        """Logout with an active session calls audit_log with the user's id."""
        fake_user = {
            "id": "user-100",
            "username": "sessionuser",
            "global_name": None,
            "avatar": None,
        }
        cookie = _make_session_cookie(fake_user)

        audit_calls: list[dict] = []

        def _capture_audit(event: str, **kwargs: object) -> None:
            audit_calls.append({"event": event, **kwargs})

        with patch("backend.server.api.auth.audit_log", side_effect=_capture_audit):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                client.cookies.set("session", cookie)
                logout_r = await client.post("/api/auth/logout")

        assert logout_r.status_code == 200
        assert logout_r.json()["ok"] is True
        # audit_log("logout", actor=...) must have been called with the user's id
        assert any(c["event"] == "logout" and c.get("actor") == "user-100" for c in audit_calls)
