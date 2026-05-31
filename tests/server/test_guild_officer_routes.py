"""HTTP-layer tests for web/routes/guild_officer.py — COV-008.

Covers:
  GET  /guild/{name}/officer-status — unauthenticated returns false; officer returns true.
  GET  /guild/{name}/claims         — 401, 403, officer gets filtered list.
  POST /guild/{name}/claims/{id}/approve — 401, 403, 404, self-approve 403, happy path.
  POST /guild/{name}/claims/{id}/reject  — 401, 403, 404, self-reject 403, happy path.
  GET  /admin/pending-users         — admin only.
  POST /admin/users/{id}/approve    — admin only, 404 for unknown.
  POST /admin/users/{id}/deny       — admin only, cannot self-deny, 404 for unknown.

Session injection via signed itsdangerous cookie (same as test_notifications.py).
Admin routes use the `_require_admin` dependency.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.users import make_fake_admin, make_fake_user

_TEST_SECRET = "pytest-session-secret-not-real-0123456789"


def _make_session_cookie(user: dict) -> str:
    import itsdangerous

    payload = json.dumps({"user": user})
    encoded = base64.b64encode(payload.encode()).decode()
    signer = itsdangerous.TimestampSigner(_TEST_SECRET)
    return signer.sign(encoded).decode()


_OFFICER = make_fake_user(id="officer-1", username="officer")
_ADMIN = make_fake_admin(id="admin-1", username="boss")
_USER = make_fake_user(id="user-1", username="regular")


def _officer_cookies() -> dict:
    return {"session": _make_session_cookie(dict(_OFFICER))}


def _admin_cookies() -> dict:
    return {"session": _make_session_cookie(dict(_ADMIN))}


def _user_cookies() -> dict:
    return {"session": _make_session_cookie(dict(_USER))}


def _fake_claim(**kwargs) -> dict:
    defaults = {
        "id": 1,
        "discord_id": "other-user",
        "discord_name": "OtherUser",
        "avatar": None,
        "character_name": "Sihtric",
        "requested_at": 1700000000,
        "status": "pending",
        "reviewed_by": None,
    }
    return {**defaults, **kwargs}


# ---------------------------------------------------------------------------
# GET /guild/{name}/officer-status
# ---------------------------------------------------------------------------


class TestGetOfficerStatus:
    async def test_unauthenticated_returns_false(self, app):
        """No session → is_officer: false (not 401)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/guild/Exordium/officer-status")
        assert r.status_code == 200
        assert r.json()["is_officer"] is False

    async def test_non_officer_returns_false(self, app):
        """Authenticated user with no officer rank → is_officer: false."""
        with patch(
            "backend.server.api.guild_officer._officer_chars",
            new=AsyncMock(return_value=set()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_user_cookies(),
            ) as client:
                r = await client.get("/api/guild/Exordium/officer-status")
        assert r.status_code == 200
        assert r.json()["is_officer"] is False

    async def test_officer_returns_true(self, app):
        """User with officer rank returns is_officer: true."""
        with patch(
            "backend.server.api.guild_officer._officer_chars",
            new=AsyncMock(return_value={"sihtric"}),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.get("/api/guild/Exordium/officer-status")
        assert r.status_code == 200
        assert r.json()["is_officer"] is True


# ---------------------------------------------------------------------------
# GET /guild/{name}/claims
# ---------------------------------------------------------------------------


class TestGetGuildClaims:
    async def test_unauthenticated_returns_401(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/guild/Exordium/claims")
        assert r.status_code == 401

    async def test_non_officer_returns_403(self, app):
        with patch(
            "backend.server.api.guild_officer._officer_chars",
            new=AsyncMock(return_value=set()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_user_cookies(),
            ) as client:
                r = await client.get("/api/guild/Exordium/claims")
        assert r.status_code == 403

    async def test_officer_gets_filtered_pending_claims(self, app):
        """Officer sees only claims whose character is in their guild's roster."""
        roster = {"sihtric": "Officer", "menludiir": "Member"}
        claims = [
            _fake_claim(character_name="Sihtric"),  # in roster
            _fake_claim(id=2, character_name="Ghost", discord_id="ghost"),  # NOT in roster
        ]
        with (
            patch(
                "backend.server.api.guild_officer._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.guild_officer._roster_rank_map",
                new=AsyncMock(return_value=roster),
            ),
            patch(
                "backend.server.api.guild_officer.list_claims",
                new=AsyncMock(return_value=claims),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.get("/api/guild/Exordium/claims")
        assert r.status_code == 200
        body = r.json()
        names = [c["character_name"] for c in body]
        assert "Sihtric" in names
        assert "Ghost" not in names

    async def test_own_claim_marked_is_own_true(self, app):
        """A claim belonging to the requesting officer has is_own=True."""
        roster = {"sihtric": "Officer"}
        own_claim = _fake_claim(discord_id=_OFFICER["id"], character_name="Sihtric")
        with (
            patch(
                "backend.server.api.guild_officer._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.guild_officer._roster_rank_map",
                new=AsyncMock(return_value=roster),
            ),
            patch(
                "backend.server.api.guild_officer.list_claims",
                new=AsyncMock(return_value=[own_claim]),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.get("/api/guild/Exordium/claims")
        body = r.json()
        assert body[0]["is_own"] is True


# ---------------------------------------------------------------------------
# POST /guild/{name}/claims/{id}/approve
# ---------------------------------------------------------------------------


class TestOfficerApproveClaim:
    async def test_unauthenticated_returns_401(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/guild/Exordium/claims/1/approve")
        assert r.status_code == 401

    async def test_non_officer_returns_403(self, app):
        with patch(
            "backend.server.api.guild_officer._officer_chars",
            new=AsyncMock(return_value=set()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_user_cookies(),
            ) as client:
                r = await client.post("/api/guild/Exordium/claims/1/approve")
        assert r.status_code == 403

    async def test_claim_not_found_returns_404(self, app):
        with (
            patch(
                "backend.server.api.guild_officer._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.guild_officer.get_claim_by_id",
                new=AsyncMock(return_value=None),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.post("/api/guild/Exordium/claims/999/approve")
        assert r.status_code == 404

    async def test_self_approve_returns_403(self, app):
        """Officer cannot approve their own claim."""
        own_claim = _fake_claim(discord_id=_OFFICER["id"])
        with (
            patch(
                "backend.server.api.guild_officer._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.guild_officer.get_claim_by_id",
                new=AsyncMock(return_value=own_claim),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.post("/api/guild/Exordium/claims/1/approve")
        assert r.status_code == 403
        assert "cannot approve your own" in r.json()["detail"]

    async def test_happy_path_approves_and_returns_claim(self, app):
        """Valid approve → 200 with the approved claim."""
        claim = _fake_claim(discord_id="other-user")
        approved = {**claim, "status": "approved"}
        with (
            patch(
                "backend.server.api.guild_officer._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.guild_officer.get_claim_by_id",
                new=AsyncMock(return_value=claim),
            ),
            patch(
                "backend.server.api.guild_officer.review_claim",
                new=AsyncMock(return_value=approved),
            ),
            patch(
                "backend.server.api.guild_officer.invalidate_user_claim_cache_all_worlds",
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.post("/api/guild/Exordium/claims/1/approve")
        assert r.status_code == 200
        assert r.json()["character_name"] == "Sihtric"


# ---------------------------------------------------------------------------
# POST /guild/{name}/claims/{id}/reject
# ---------------------------------------------------------------------------


class TestOfficerRejectClaim:
    async def test_unauthenticated_returns_401(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/guild/Exordium/claims/1/reject", json={"note": None})
        assert r.status_code == 401

    async def test_non_officer_returns_403(self, app):
        with patch(
            "backend.server.api.guild_officer._officer_chars",
            new=AsyncMock(return_value=set()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_user_cookies(),
            ) as client:
                r = await client.post("/api/guild/Exordium/claims/1/reject", json={"note": None})
        assert r.status_code == 403

    async def test_self_reject_returns_403(self, app):
        """Officer cannot reject their own claim."""
        own_claim = _fake_claim(discord_id=_OFFICER["id"])
        with (
            patch(
                "backend.server.api.guild_officer._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.guild_officer.get_claim_by_id",
                new=AsyncMock(return_value=own_claim),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.post("/api/guild/Exordium/claims/1/reject", json={"note": None})
        assert r.status_code == 403

    async def test_happy_path_rejects_and_returns_ok(self, app):
        """Valid reject → 200 with ok."""
        claim = _fake_claim(discord_id="other-user")
        rejected = {**claim, "status": "rejected"}
        with (
            patch(
                "backend.server.api.guild_officer._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.guild_officer.get_claim_by_id",
                new=AsyncMock(return_value=claim),
            ),
            patch(
                "backend.server.api.guild_officer.review_claim",
                new=AsyncMock(return_value=rejected),
            ),
            patch(
                "backend.server.api.guild_officer.invalidate_user_claim_cache_all_worlds",
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.post("/api/guild/Exordium/claims/1/reject", json={"note": "Wrong character"})
        assert r.status_code == 200
        assert r.json()["ok"] is True


# ---------------------------------------------------------------------------
# GET /admin/pending-users
# ---------------------------------------------------------------------------


class TestGetPendingUsers:
    async def test_non_admin_returns_403(self, app):
        with patch(
            "backend.server.api.guild_officer._officer_chars",
            new=AsyncMock(return_value=set()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_user_cookies(),
            ) as client:
                r = await client.get("/api/admin/pending-users")
        assert r.status_code == 403

    async def test_admin_gets_pending_user_list(self, app):
        """Admin sees the list of pending users."""
        fake_users = [
            {
                "discord_id": "new-user",
                "discord_name": "NewUser",
                "discord_username": "newuser",
                "avatar": None,
                "first_seen": 1700000000,
            }
        ]
        with (
            patch("backend.server.api.guild_officer._require_admin", return_value=dict(_ADMIN)),
            patch(
                "backend.server.api.guild_officer.list_pending_users",
                new=AsyncMock(return_value=fake_users),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_admin_cookies(),
            ) as client:
                r = await client.get("/api/admin/pending-users")
        assert r.status_code == 200
        body = r.json()
        assert body[0]["discord_id"] == "new-user"


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/approve + /deny
# ---------------------------------------------------------------------------


class TestAdminUserAccess:
    async def test_approve_user_happy_path(self, app):
        """Admin approves a pending user → 200 ok."""
        with (
            patch("backend.server.api.guild_officer._require_admin", return_value=dict(_ADMIN)),
            patch(
                "backend.server.api.guild_officer.set_user_access",
                new=AsyncMock(return_value=True),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_admin_cookies(),
            ) as client:
                r = await client.post("/api/admin/users/new-user/approve")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    async def test_approve_user_not_found_returns_404(self, app):
        with (
            patch("backend.server.api.guild_officer._require_admin", return_value=dict(_ADMIN)),
            patch(
                "backend.server.api.guild_officer.set_user_access",
                new=AsyncMock(return_value=False),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_admin_cookies(),
            ) as client:
                r = await client.post("/api/admin/users/ghost-user/approve")
        assert r.status_code == 404

    async def test_deny_user_happy_path(self, app):
        """Admin denies a user (different from self) → 200 ok."""
        with (
            patch("backend.server.api.guild_officer._require_admin", return_value=dict(_ADMIN)),
            patch(
                "backend.server.api.guild_officer.set_user_access",
                new=AsyncMock(return_value=True),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_admin_cookies(),
            ) as client:
                r = await client.post("/api/admin/users/other-user/deny")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    async def test_deny_own_account_returns_400(self, app):
        """Admin cannot deny their own account → 400."""
        with patch("backend.server.api.guild_officer._require_admin", return_value=dict(_ADMIN)):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_admin_cookies(),
            ) as client:
                r = await client.post(f"/api/admin/users/{_ADMIN['id']}/deny")
        assert r.status_code == 400
        assert "cannot deny your own" in r.json()["detail"]

    async def test_deny_unknown_user_returns_404(self, app):
        with (
            patch("backend.server.api.guild_officer._require_admin", return_value=dict(_ADMIN)),
            patch(
                "backend.server.api.guild_officer.set_user_access",
                new=AsyncMock(return_value=False),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_admin_cookies(),
            ) as client:
                r = await client.post("/api/admin/users/ghost-user/deny")
        assert r.status_code == 404
