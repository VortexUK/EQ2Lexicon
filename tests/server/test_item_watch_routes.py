"""HTTP-layer tests for web/routes/item_watch.py — COV-005.

Covers:
  GET  /guild/{name}/item-watch  — unauthenticated → 401, non-officer → 403,
                                    officer → returns list.
  POST /guild/{name}/item-watch  — character not in roster → 404,
                                    item not found → 404, duplicate → 409,
                                    happy path creates watch.
  DELETE /guild/{name}/item-watch/{id} — officer gate; removes; 404 for missing.

All Census + DB helpers are mocked — the HTTP layer only, no real DB or
network calls.

Session injection: the item_watch route reads `request.session.get("user")`
directly so we inject a signed itsdangerous cookie (same pattern as
test_notifications.py) matching the test app secret.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import itsdangerous
import pytest
from httpx import ASGITransport, AsyncClient

# Matches the fixed secret used by the `app` fixture in tests/conftest.py.
_TEST_SECRET = "pytest-session-secret-not-real-0123456789"


def _make_session_cookie(user: dict) -> str:
    """Return a Starlette-compatible signed session cookie value for `user`."""
    payload = json.dumps({"user": user})
    encoded = base64.b64encode(payload.encode()).decode()
    signer = itsdangerous.TimestampSigner(_TEST_SECRET)
    return signer.sign(encoded).decode()


_OFFICER_USER = {"id": "officer-1", "username": "officer"}
_REGULAR_USER = {"id": "user-1", "username": "regular"}


def _officer_cookies() -> dict:
    return {"session": _make_session_cookie(_OFFICER_USER)}


def _user_cookies() -> dict:
    return {"session": _make_session_cookie(_REGULAR_USER)}


def _fake_watch_row(**kwargs) -> dict:
    defaults = {
        "id": 1,
        "guild_name": "Exordium",
        "character_name": "Sihtric",
        "item_id": 12345,
        "item_name": "Fabled Ring",
        "added_by": "officer-1",
        "added_by_name": "Sihtric",
        "world": "Varsoon",
        "added_at": 1700000000,
        "first_seen_at": None,
        "last_seen_at": None,
        "last_checked_at": None,
    }
    return {**defaults, **kwargs}


# ---------------------------------------------------------------------------
# GET /guild/{name}/item-watch
# ---------------------------------------------------------------------------


class TestGetItemWatches:
    async def test_unauthenticated_returns_401(self, app):
        """No session cookie → 401."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/guild/Exordium/item-watch")
        assert r.status_code == 401

    async def test_non_officer_returns_403(self, app):
        """Authenticated but not an officer of the guild → 403."""
        with patch(
            "backend.server.api.item_watch._officer_chars",
            new=AsyncMock(return_value=set()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_user_cookies(),
            ) as client:
                r = await client.get("/api/guild/Exordium/item-watch")
        assert r.status_code == 403

    async def test_officer_returns_watch_list(self, app):
        """Officer of the guild gets the list of watch entries."""
        watch = _fake_watch_row()
        with (
            patch(
                "backend.server.api.item_watch._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.item_watch.list_item_watches",
                new=AsyncMock(return_value=[watch]),
            ),
            patch(
                "backend.server.api.item_watch._check_all_watches",
                new=AsyncMock(),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.get("/api/guild/Exordium/item-watch")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert body[0]["item_name"] == "Fabled Ring"

    async def test_officer_returns_empty_list_when_no_watches(self, app):
        """No watches yet → officer gets an empty list, not a 404."""
        with (
            patch(
                "backend.server.api.item_watch._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.item_watch.list_item_watches",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "backend.server.api.item_watch._check_all_watches",
                new=AsyncMock(),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.get("/api/guild/Exordium/item-watch")
        assert r.status_code == 200
        assert r.json() == []


# ---------------------------------------------------------------------------
# POST /guild/{name}/item-watch
# ---------------------------------------------------------------------------


class TestAddItemWatch:
    async def test_unauthenticated_returns_401(self, app):
        """No session → 401."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/api/guild/Exordium/item-watch",
                json={"character_name": "Sihtric", "item_name": "Fabled Ring"},
            )
        assert r.status_code == 401

    async def test_non_officer_returns_403(self, app):
        """Non-officer cannot add a watch."""
        with patch(
            "backend.server.api.item_watch._officer_chars",
            new=AsyncMock(return_value=set()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_user_cookies(),
            ) as client:
                r = await client.post(
                    "/api/guild/Exordium/item-watch",
                    json={"character_name": "Sihtric", "item_name": "Fabled Ring"},
                )
        assert r.status_code == 403

    async def test_character_not_in_roster_returns_404(self, app):
        """Character name not found in the guild roster → 404."""
        with (
            patch(
                "backend.server.api.item_watch._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.item_watch._roster_rank_map",
                new=AsyncMock(return_value={}),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.post(
                    "/api/guild/Exordium/item-watch",
                    json={"character_name": "Nobody", "item_name": "Fabled Ring"},
                )
        assert r.status_code == 404
        assert "not a member" in r.json()["detail"]

    async def test_item_not_found_returns_404(self, app):
        """Item not in local DB and Census returns nothing → 404."""
        with (
            patch(
                "backend.server.api.item_watch._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.item_watch._roster_rank_map",
                new=AsyncMock(return_value={"sihtric": "Officer"}),
            ),
            patch("backend.eq2db.items.catalogue.find_by_name", new=AsyncMock(return_value=None)),
            patch("backend.server.api.item_watch.shared_census_client") as mock_ctx,
        ):
            mock_client = AsyncMock()
            mock_client.get_raw_item = AsyncMock(return_value=None)
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.post(
                    "/api/guild/Exordium/item-watch",
                    json={"character_name": "Sihtric", "item_name": "GhostItem"},
                )
        assert r.status_code == 404
        assert "not found" in r.json()["detail"]

    async def test_duplicate_watch_returns_409(self, app):
        """Adding the same item for the same character again → 409."""
        fake_item_raw = {"id": "12345", "displayname": "Fabled Ring"}
        with (
            patch(
                "backend.server.api.item_watch._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.item_watch._roster_rank_map",
                new=AsyncMock(return_value={"sihtric": "Officer"}),
            ),
            patch("backend.eq2db.items.catalogue.find_by_name", new=AsyncMock(return_value=fake_item_raw)),
            patch(
                "backend.server.api.item_watch.get_active_claims",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "backend.server.api.item_watch.get_primary_claim",
                return_value=None,
            ),
            patch(
                "backend.server.api.item_watch.guild_cache.get_stale",
                return_value=(None, False),
            ),
            patch(
                "backend.server.api.item_watch.add_item_watch",
                new=AsyncMock(side_effect=ValueError("already being watched")),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.post(
                    "/api/guild/Exordium/item-watch",
                    json={"character_name": "Sihtric", "item_name": "Fabled Ring"},
                )
        assert r.status_code == 409
        assert "already being watched" in r.json()["detail"]

    async def test_happy_path_creates_watch_and_returns_201(self, app):
        """Valid request from officer → 201 with the new watch entry."""
        fake_item_raw = {"id": "12345", "displayname": "Fabled Ring"}
        new_row = _fake_watch_row()
        with (
            patch(
                "backend.server.api.item_watch._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.item_watch._roster_rank_map",
                new=AsyncMock(return_value={"sihtric": "Officer"}),
            ),
            patch("backend.eq2db.items.catalogue.find_by_name", new=AsyncMock(return_value=fake_item_raw)),
            patch(
                "backend.server.api.item_watch.get_active_claims",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "backend.server.api.item_watch.get_primary_claim",
                return_value=None,
            ),
            patch(
                "backend.server.api.item_watch.guild_cache.get_stale",
                return_value=(None, False),
            ),
            patch(
                "backend.server.api.item_watch.add_item_watch",
                new=AsyncMock(return_value=new_row),
            ),
            patch(
                "backend.server.api.item_watch._check_watch",
                new=AsyncMock(),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.post(
                    "/api/guild/Exordium/item-watch",
                    json={"character_name": "Sihtric", "item_name": "Fabled Ring"},
                )
        assert r.status_code == 201
        body = r.json()
        assert body["item_name"] == "Fabled Ring"
        assert body["character_name"] == "Sihtric"


# ---------------------------------------------------------------------------
# DELETE /guild/{name}/item-watch/{watch_id}
# ---------------------------------------------------------------------------


class TestDeleteItemWatch:
    async def test_unauthenticated_returns_401(self, app):
        """No session → 401."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.delete("/api/guild/Exordium/item-watch/1")
        assert r.status_code == 401

    async def test_non_officer_returns_403(self, app):
        """Non-officer cannot delete a watch."""
        with patch(
            "backend.server.api.item_watch._officer_chars",
            new=AsyncMock(return_value=set()),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_user_cookies(),
            ) as client:
                r = await client.delete("/api/guild/Exordium/item-watch/1")
        assert r.status_code == 403

    async def test_missing_watch_returns_404(self, app):
        """Watch entry not found (wrong guild or id) → 404."""
        with (
            patch(
                "backend.server.api.item_watch._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.item_watch.remove_item_watch",
                new=AsyncMock(return_value=False),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.delete("/api/guild/Exordium/item-watch/999")
        assert r.status_code == 404

    async def test_officer_can_delete_watch(self, app):
        """Officer deletes an existing watch → 200 with ok."""
        with (
            patch(
                "backend.server.api.item_watch._officer_chars",
                new=AsyncMock(return_value={"sihtric"}),
            ),
            patch(
                "backend.server.api.item_watch.remove_item_watch",
                new=AsyncMock(return_value=True),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                cookies=_officer_cookies(),
            ) as client:
                r = await client.delete("/api/guild/Exordium/item-watch/1")
        assert r.status_code == 200
        assert r.json()["ok"] is True
