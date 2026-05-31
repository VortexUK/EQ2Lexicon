"""Behavioural tests for GET /api/notifications.

Polled every 60 s from the frontend bell icon. Zero tests existed before
the COV-001 audit finding. Each test pins one user-facing branch of the
endpoint per the audit's proposed-scenario list.

All Census calls are stubbed — the endpoint must never hit Census on a poll
(the cache-miss / refresh path lives in the read endpoints). The session is
injected via a signed itsdangerous cookie matching the test app's secret.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import itsdangerous
import pytest
from httpx import ASGITransport, AsyncClient

# Matches the fixed secret used by the `app` fixture in tests/conftest.py
# (create_app(session_secret="pytest-session-secret-not-real-0123456789")).
_TEST_SECRET = "pytest-session-secret-not-real-0123456789"


def _make_session_cookie(user: dict) -> str:
    """Return a Starlette-compatible signed session cookie value for `user`.

    Replicates the SessionMiddleware encoding:
      cookie = TimestampSigner(secret).sign(b64encode(json(session_dict)))
    """
    payload = json.dumps({"user": user})
    encoded = base64.b64encode(payload.encode()).decode()
    signer = itsdangerous.TimestampSigner(_TEST_SECRET)
    return signer.sign(encoded).decode()


async def _get_notifications(app, user: dict | None) -> dict:
    """Hit GET /api/notifications with an optional signed session and return the JSON body."""
    cookies = {}
    if user is not None:
        cookies["session"] = _make_session_cookie(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as client:
        r = await client.get("/api/notifications")
    assert r.status_code == 200
    return r.json()


# ---------------------------------------------------------------------------
# Scenario 1 — unauthenticated
# ---------------------------------------------------------------------------


async def test_notifications_unauthenticated_returns_zero(app):
    """Bell stays quiet when no user is signed in (returns 200 with all-zero counts,
    not 401 — the endpoint is designed to be polled without auth)."""
    body = await _get_notifications(app, user=None)
    assert body["pending_claims"] == 0
    assert body["pending_users"] == 0
    assert body["officer_guild"] is None


# ---------------------------------------------------------------------------
# Scenario 2 — admin-only (no officer role)
# ---------------------------------------------------------------------------


async def test_notifications_admin_only_reports_pending_users(app):
    """Admin user with no officer claims sees pending_users populated,
    pending_claims stays zero."""
    import backend.server.api.notifications as nmod

    user = {"id": "admin-1", "username": "boss"}
    fake_pending = [{"id": "u1"}, {"id": "u2"}, {"id": "u3"}]

    with (
        patch.object(nmod, "_ADMIN_IDS", frozenset({"admin-1"})),
        patch("backend.server.api.notifications.list_pending_users", new=AsyncMock(return_value=fake_pending)),
        patch("backend.server.api.notifications.get_active_claims", new=AsyncMock(return_value={"approved": []})),
    ):
        body = await _get_notifications(app, user=user)

    assert body["pending_users"] == 3
    assert body["pending_claims"] == 0
    assert body["officer_guild"] is None


# ---------------------------------------------------------------------------
# Scenario 3 — officer reports guild claims
# ---------------------------------------------------------------------------


async def test_notifications_officer_reports_guild_claims(app):
    """Non-admin officer user sees pending_claims for their guild."""
    import backend.server.api.notifications as nmod

    user = {"id": "user-1", "username": "knight"}
    # User has one approved character claim: "Sihtric"
    approved = [{"character_name": "Sihtric"}]
    # The character cache returns a cached char with guild_name
    cached_char = MagicMock()
    cached_char.guild_name = "Exordium"
    # Rank map: Sihtric is an officer (rank_id 0 is in _OFFICER_RANKS = {0, 1})
    rank_map = {"sihtric": 0}
    # 2 pending claims for Sihtric
    pending_claims = [
        {"id": 10, "character_name": "Sihtric"},
        {"id": 11, "character_name": "Sihtric"},
    ]

    with (
        patch.object(nmod, "_ADMIN_IDS", frozenset()),
        patch("backend.server.api.notifications.list_pending_users", new=AsyncMock(return_value=[])),
        patch("backend.server.api.notifications.get_active_claims", new=AsyncMock(return_value={"approved": approved})),
        patch("backend.server.api.notifications.character_cache") as mock_cache,
        patch("backend.server.api.notifications._roster_rank_map", new=AsyncMock(return_value=rank_map)),
        patch("backend.server.api.notifications.list_claims", new=AsyncMock(return_value=pending_claims)),
    ):
        mock_cache.get_stale.return_value = (cached_char, False)
        body = await _get_notifications(app, user=user)

    assert body["pending_claims"] == 2
    assert body["officer_guild"] == "Exordium"
    assert body["pending_users"] == 0


# ---------------------------------------------------------------------------
# Scenario 4 — dedupes claims across multi-guild officer
# ---------------------------------------------------------------------------


async def test_notifications_dedupes_claims_across_multi_guild_officer(app):
    """When the same claim ID appears in two guild rank-maps, it is counted only once."""
    import backend.server.api.notifications as nmod

    user = {"id": "user-2", "username": "dual_officer"}
    approved = [{"character_name": "Menludiir"}, {"character_name": "Sihtric"}]
    cached_guild_a = MagicMock()
    cached_guild_a.guild_name = "GuildA"
    cached_guild_b = MagicMock()
    cached_guild_b.guild_name = "GuildB"

    # Both characters are officers
    rank_map_a = {"menludiir": 0}
    rank_map_b = {"sihtric": 1}

    # The same claim (id=99) is in both guilds' pending lists
    pending_claims = [
        {"id": 99, "character_name": "Menludiir"},
        {"id": 99, "character_name": "Sihtric"},
    ]

    call_count = 0

    async def _fake_rank_map(guild_name: str) -> dict:
        nonlocal call_count
        call_count += 1
        return rank_map_a if guild_name == "GuildA" else rank_map_b

    def _fake_get_stale(key: str):
        if "menludiir" in key.lower():
            return (cached_guild_a, False)
        if "sihtric" in key.lower():
            return (cached_guild_b, False)
        return (None, False)

    with (
        patch.object(nmod, "_ADMIN_IDS", frozenset()),
        patch("backend.server.api.notifications.list_pending_users", new=AsyncMock(return_value=[])),
        patch("backend.server.api.notifications.get_active_claims", new=AsyncMock(return_value={"approved": approved})),
        patch("backend.server.api.notifications.character_cache") as mock_cache,
        patch("backend.server.api.notifications._roster_rank_map", new=_fake_rank_map),
        patch("backend.server.api.notifications.list_claims", new=AsyncMock(return_value=pending_claims)),
    ):
        mock_cache.get_stale.side_effect = _fake_get_stale
        body = await _get_notifications(app, user=user)

    # The claim id=99 must not be double-counted
    assert body["pending_claims"] == 1


# ---------------------------------------------------------------------------
# Scenario 5 — cache miss for approved character (no crash)
# ---------------------------------------------------------------------------


async def test_notifications_skips_unresolved_character_cache_misses(app):
    """If the approved character has no cache entry, the endpoint returns safely
    with zero counts — no KeyError or AttributeError."""
    import backend.server.api.notifications as nmod

    user = {"id": "user-3", "username": "cachemiss"}
    approved = [{"character_name": "GhostChar"}]

    with (
        patch.object(nmod, "_ADMIN_IDS", frozenset()),
        patch("backend.server.api.notifications.list_pending_users", new=AsyncMock(return_value=[])),
        patch("backend.server.api.notifications.get_active_claims", new=AsyncMock(return_value={"approved": approved})),
        patch("backend.server.api.notifications.character_cache") as mock_cache,
    ):
        # Cache miss — character not resolved yet
        mock_cache.get_stale.return_value = (None, False)
        body = await _get_notifications(app, user=user)

    assert body["pending_claims"] == 0
    assert body["officer_guild"] is None


# ---------------------------------------------------------------------------
# Scenario 6 — admin who is also an officer (combines both counts)
# ---------------------------------------------------------------------------


async def test_notifications_admin_who_is_also_officer_combines(app):
    """Admin user who is also an officer in a guild gets both pending_users
    and pending_claims populated in a single response."""
    import backend.server.api.notifications as nmod

    user = {"id": "admin-officer", "username": "commandos"}
    approved = [{"character_name": "Vortex"}]
    cached_char = MagicMock()
    cached_char.guild_name = "Raiders"
    rank_map = {"vortex": 0}
    pending_claims = [{"id": 5, "character_name": "Vortex"}]
    fake_pending_users = [{"id": "new-user-1"}]

    with (
        patch.object(nmod, "_ADMIN_IDS", frozenset({"admin-officer"})),
        patch("backend.server.api.notifications.list_pending_users", new=AsyncMock(return_value=fake_pending_users)),
        patch("backend.server.api.notifications.get_active_claims", new=AsyncMock(return_value={"approved": approved})),
        patch("backend.server.api.notifications.character_cache") as mock_cache,
        patch("backend.server.api.notifications._roster_rank_map", new=AsyncMock(return_value=rank_map)),
        patch("backend.server.api.notifications.list_claims", new=AsyncMock(return_value=pending_claims)),
    ):
        mock_cache.get_stale.return_value = (cached_char, False)
        body = await _get_notifications(app, user=user)

    assert body["pending_users"] == 1
    assert body["pending_claims"] == 1
    assert body["officer_guild"] == "Raiders"
