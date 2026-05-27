from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from web.app import create_app

_SECRET = "test-secret-fixed"


@pytest.fixture
def app():
    return create_app(session_secret=_SECRET)


@pytest.mark.asyncio
async def test_me_unauthenticated(app):
    """No session → 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_callback_then_me(app):
    """OAuth callback sets session; /api/auth/me then returns the user."""
    fake_user = {
        "id": "123456789",
        "username": "testuser",
        "global_name": "Test User",
        "avatar": None,
    }

    mock_token = MagicMock()
    mock_token.status_code = 200
    mock_token.json.return_value = {"access_token": "fake-token"}

    mock_user = MagicMock()
    mock_user.status_code = 200
    mock_user.json.return_value = fake_user

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_token)
    mock_http.get = AsyncMock(return_value=mock_user)

    with patch("web.routes.auth.httpx.AsyncClient") as MockHttpx:
        MockHttpx.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        MockHttpx.return_value.__aexit__ = AsyncMock(return_value=False)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
        ) as client:
            # Hit login first so the CSRF state is written into the session cookie.
            login_r = await client.get("/api/auth/login")
            assert login_r.status_code in (302, 307)
            from urllib.parse import parse_qs, urlparse

            login_location = login_r.headers["location"]
            state = parse_qs(urlparse(login_location).query).get("state", [None])[0]
            assert state is not None, "login redirect must include state param"

            cb = await client.get(f"/api/auth/callback?code=fake&state={state}")
            assert cb.status_code in (302, 307)
            session_cookie = cb.cookies.get("session")
            assert session_cookie is not None, "callback must set a session cookie"

            me = await client.get("/api/auth/me")
            assert me.status_code == 200
            data = me.json()
            assert data["id"] == "123456789"
            assert data["username"] == "testuser"
            # Fresh user: no DB-granted roles yet — keep this assertion so we
            # notice if the default ever silently changes.
            assert data["static_roles"] == []


@pytest.mark.asyncio
async def test_me_includes_granted_roles(app):
    """A user with a contributor role row should see it on /auth/me."""
    with (
        patch(
            "web.routes.auth.list_roles_for_user",
            return_value=["contributor"],
        ),
        patch("web.routes.auth.get_user_access_status", return_value="approved"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Plant a session directly via the SessionMiddleware test pattern
            # is awkward — easier to override the session-reading code path.
            from starlette.requests import Request as _Req  # noqa: F401

            # Use the OAuth-callback flow shape: set session, then GET /me.
            # Mock both Discord HTTP calls so the callback succeeds, then
            # /me reads the planted session.
            mock_token = MagicMock()
            mock_token.status_code = 200
            mock_token.json.return_value = {"access_token": "fake"}
            mock_user_payload = MagicMock()
            mock_user_payload.status_code = 200
            mock_user_payload.json.return_value = {
                "id": "777",
                "username": "contrib",
                "global_name": None,
                "avatar": None,
            }
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_token)
            mock_http.get = AsyncMock(return_value=mock_user_payload)
            with patch("web.routes.auth.httpx.AsyncClient") as MockHttpx:
                MockHttpx.return_value.__aenter__ = AsyncMock(return_value=mock_http)
                MockHttpx.return_value.__aexit__ = AsyncMock(return_value=False)
                login_r = await client.get("/api/auth/login")
                from urllib.parse import parse_qs, urlparse

                state = parse_qs(urlparse(login_r.headers["location"]).query)["state"][0]
                await client.get(f"/api/auth/callback?code=fake&state={state}")
                me = await client.get("/api/auth/me")
            assert me.status_code == 200
            data = me.json()
            assert data["static_roles"] == ["contributor"]


@pytest.mark.asyncio
async def test_logout(app):
    """Logout always returns ok."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/auth/logout")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_login_redirects_to_discord(app):
    """Login endpoint should redirect to discord.com."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
        response = await client.get("/api/auth/login")
    assert response.status_code in (302, 307)
    assert "discord.com" in response.headers["location"]
