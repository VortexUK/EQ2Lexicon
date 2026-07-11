"""Tests for /api/auth/tokens — mint, list, revoke."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.users_db import point_users_db_at


def _fake_session_user(request=None) -> dict:
    return {"id": "123456789", "username": "testuser"}


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tokens_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/auth/tokens")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_mint_token_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/auth/tokens", json={"name": "Test"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_revoke_token_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.delete("/api/auth/tokens/1")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mint_returns_raw_token_once(app):
    fake_row = {
        "id": 1,
        "name": "Desktop ACT",
        "token_prefix": "eq2c_abcdefg",
        "created_at": 1716561200,
        "last_used_at": None,
        "revoked_at": None,
    }
    raw_token = "eq2c_fakefakefakefakefakefakefakefakefake"

    with (
        patch("backend.server.api.auth_tokens._require_user", _fake_session_user),
        patch(
            "backend.server.api.auth_tokens.users_db.mint_api_token",
            new=AsyncMock(return_value=(raw_token, fake_row)),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/auth/tokens", json={"name": "Desktop ACT"})

    assert r.status_code == 201
    data = r.json()
    assert data["token"] == raw_token  # raw token returned exactly once
    assert data["row"]["name"] == "Desktop ACT"
    assert data["row"]["token_prefix"] == "eq2c_abcdefg"
    assert data["row"]["revoked_at"] is None


@pytest.mark.asyncio
async def test_mint_rejects_empty_name(app):
    with patch("backend.server.api.auth_tokens._require_user", _fake_session_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Empty after strip
            r = await client.post("/api/auth/tokens", json={"name": "   "})
    # Pydantic min_length=1 catches actual empty, our strip+check catches whitespace.
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_list_returns_users_tokens(app):
    fake_rows = [
        {
            "id": 2,
            "name": "Newer",
            "token_prefix": "eq2c_2222222",
            "created_at": 1716561300,
            "last_used_at": None,
            "revoked_at": None,
        },
        {
            "id": 1,
            "name": "Older",
            "token_prefix": "eq2c_1111111",
            "created_at": 1716561200,
            "last_used_at": 1716561500,
            "revoked_at": None,
        },
    ]
    with (
        patch("backend.server.api.auth_tokens._require_user", _fake_session_user),
        patch(
            "backend.server.api.auth_tokens.users_db.list_api_tokens",
            new=AsyncMock(return_value=fake_rows),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/auth/tokens")
    assert r.status_code == 200
    data = r.json()
    assert len(data["tokens"]) == 2
    assert data["tokens"][0]["name"] == "Newer"
    assert data["tokens"][1]["last_used_at"] == 1716561500


@pytest.mark.asyncio
async def test_revoke_success(app):
    with (
        patch("backend.server.api.auth_tokens._require_user", _fake_session_user),
        patch(
            "backend.server.api.auth_tokens.users_db.revoke_api_token",
            new=AsyncMock(return_value=True),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/auth/tokens/1")
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_revoke_404_when_token_missing_or_not_yours(app):
    with (
        patch("backend.server.api.auth_tokens._require_user", _fake_session_user),
        patch(
            "backend.server.api.auth_tokens.users_db.revoke_api_token",
            new=AsyncMock(return_value=False),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/auth/tokens/9999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# DB-layer unit tests (use a temp file DB)
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_users_db(tmp_path, monkeypatch):
    db_path = tmp_path / "users.db"
    from backend.server import db as users_db

    # init_db creates schema; point every domain store at the temp path.
    users_db.init_db(db_path)
    point_users_db_at(monkeypatch, db_path)
    # Seed a user row so the FK on api_tokens.user_id is satisfied.
    import sqlite3 as _sqlite3

    with _sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO users (discord_id, discord_name, access_status) VALUES (?, ?, ?)",
            ("user-123", "Alice", "approved"),
        )
        conn.commit()
    return db_path


@pytest.mark.asyncio
async def test_db_mint_then_lookup_then_revoke(tmp_users_db):
    from backend.server import db as users_db

    raw, row = await users_db.mint_api_token("user-123", "First")
    assert raw.startswith("eq2c_")
    assert row["name"] == "First"
    assert row["revoked_at"] is None

    # Lookup with raw → returns user info
    found = await users_db.lookup_api_token(raw)
    assert found is not None
    assert found["user_id"] == "user-123"
    assert found["discord_name"] == "Alice"

    # Revoke
    ok = await users_db.revoke_api_token("user-123", row["id"])
    assert ok is True

    # Revoked token no longer resolves
    after = await users_db.lookup_api_token(raw)
    assert after is None


@pytest.mark.asyncio
async def test_db_lookup_rejects_garbage(tmp_users_db):
    from backend.server import db as users_db

    assert await users_db.lookup_api_token("") is None
    assert await users_db.lookup_api_token("not-our-prefix-foo") is None
    assert await users_db.lookup_api_token("eq2c_nonexistent") is None


@pytest.mark.asyncio
async def test_db_revoke_scoped_to_user(tmp_users_db):
    """A user can't revoke another user's token."""
    import sqlite3 as _sqlite3

    from backend.server import db as users_db

    with _sqlite3.connect(tmp_users_db) as conn:
        conn.execute(
            "INSERT INTO users (discord_id, discord_name, access_status) VALUES (?, ?, ?)",
            ("user-456", "Bob", "approved"),
        )
        conn.commit()

    raw_alice, row_alice = await users_db.mint_api_token("user-123", "Alice's")
    # Bob tries to revoke Alice's token
    ok = await users_db.revoke_api_token("user-456", row_alice["id"])
    assert ok is False
    # Token still works for Alice
    assert await users_db.lookup_api_token(raw_alice) is not None


# ---------------------------------------------------------------------------
# /auth/whoami — used by the ACT plugin's Test Connection button.
# The plugin reads `is_admin` (drives the Server URL edit gate) and
# `allowed_servers` (drives the ALLOWED SERVERS card + the blacklist
# editor's server dropdown). Both must be present and stable.
# ---------------------------------------------------------------------------


async def _fake_token_user(request=None) -> dict:
    return {
        "id": "user-non-admin",
        "username": "alice",
        "discord_name": "alice",
        "auth_source": "token",
    }


@pytest.mark.asyncio
async def test_whoami_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/auth/whoami")
    # No session cookie and no Bearer header → 401.
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_whoami_returns_is_admin_false_for_non_admin(app):
    """Non-admin user: is_admin=False. Stable contract — the plugin's
    Server URL field stays locked while this is False."""
    with patch("backend.server.api.auth_tokens.require_user_session_or_token", _fake_token_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/auth/whoami")
    assert r.status_code == 200
    data = r.json()
    assert data["discord_id"] == "user-non-admin"
    assert data["discord_name"] == "alice"
    assert data["auth_source"] == "token"
    assert data["is_admin"] is False


@pytest.mark.asyncio
async def test_whoami_returns_is_admin_true_for_admin(app):
    """Admin user: is_admin=True. is_admin() is the module-imported
    ADMIN_IDS frozenset check; we patch it to avoid depending on env
    vars at test time."""
    with (
        patch("backend.server.api.auth_tokens.require_user_session_or_token", _fake_token_user),
        patch("backend.server.api.auth_tokens.is_admin", return_value=True),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/auth/whoami")
    assert r.status_code == 200
    assert r.json()["is_admin"] is True


@pytest.mark.asyncio
async def test_whoami_returns_allowed_servers_sorted(app):
    """allowed_servers must be a deterministic order (sorted). The
    plugin renders the list verbatim and a server reshuffle on each
    call would jitter the UI."""
    with (
        patch("backend.server.api.auth_tokens.require_user_session_or_token", _fake_token_user),
        # Pin the ALLOWED_SERVERS constant the route reads so this test
        # passes regardless of the test runner's env. frozenset doesn't
        # guarantee iteration order, so the route is responsible for
        # sorting on the way out.
        patch("backend.server.api.auth_tokens.ALLOWED_SERVERS", frozenset({"Wuoshi", "Varsoon"})),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/auth/whoami")
    assert r.status_code == 200
    assert r.json()["allowed_servers"] == ["Varsoon", "Wuoshi"]


@pytest.mark.asyncio
async def test_whoami_returns_empty_allowed_servers_when_unset(app):
    """ALLOWED_SERVERS=frozenset() → returns []. Edge case: a
    deployment that explicitly disables uploads. The plugin shows the
    empty list with its '(none — uploads disabled until the site
    grants access)' placeholder."""
    with (
        patch("backend.server.api.auth_tokens.require_user_session_or_token", _fake_token_user),
        patch("backend.server.api.auth_tokens.ALLOWED_SERVERS", frozenset()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/auth/whoami")
    assert r.status_code == 200
    assert r.json()["allowed_servers"] == []
