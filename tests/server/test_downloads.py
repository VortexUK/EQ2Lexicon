"""Download-counter DB layer + API tests.

DB layer runs against a temp users.db (which also proves the download_events
schema block passes _assertions.py via init_db). The API is tested with the
``app`` fixture: a signed session cookie for auth + mocked db helpers (same
pattern as test_favorites.py).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import itsdangerous
import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.db import init_db
from backend.server.db.downloads import store as dl
from tests.fixtures.users_db import point_users_db_at

_TEST_SECRET = "pytest-session-secret-not-real-0123456789"


# ---------------------------------------------------------------------------
# DB layer (temp users.db)
# ---------------------------------------------------------------------------


@pytest.fixture
def users_db(tmp_path) -> Path:
    db = tmp_path / "users.db"
    init_db(db)  # creates download_events (+ asserts schema completeness)
    return db


@pytest.fixture(autouse=True)
def _stores_at_tmp(users_db: Path, monkeypatch: pytest.MonkeyPatch):
    """Point users.db (constant + every domain store) at this test's temp DB."""
    point_users_db_at(monkeypatch, users_db)


async def test_record_is_idempotent_per_user(users_db):
    assert await dl.record_download("disc1", "parser-setup") is True
    assert await dl.record_download("disc1", "parser-setup") is False  # replay
    assert await dl.count_for_slug("parser-setup") == 1


async def test_count_is_distinct_downloaders(users_db):
    """One person re-clicking never inflates the count."""
    await dl.record_download("disc1", "parser-setup")
    await dl.record_download("disc2", "parser-setup")
    await dl.record_download("disc2", "parser-setup")  # same user, again
    assert await dl.count_for_slug("parser-setup") == 2


async def test_counts_aggregates_slugs(users_db):
    await dl.record_download("disc1", "parser-setup")
    await dl.record_download("disc2", "parser-setup")
    await dl.record_download("disc1", "act-plugin")
    assert await dl.counts() == {"parser-setup": 2, "act-plugin": 1}


async def test_untouched_slug_is_zero(users_db):
    assert await dl.count_for_slug("parser-portable") == 0
    assert await dl.counts() == {}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _cookies(user: dict) -> dict:
    payload = base64.b64encode(json.dumps({"user": user}).encode()).decode()
    signed = itsdangerous.TimestampSigner(_TEST_SECRET).sign(payload).decode()
    return {"session": signed}


_USER = {"id": "disc-1", "username": "tester"}
_PATCH_BASE = "backend.server.api.downloads"


async def test_get_counts_returns_all_slugs(app):
    """The response always carries every allowlisted slug — an untouched one is
    0 (so the frontend has a stable shape to read)."""
    with patch(f"{_PATCH_BASE}.downloads_db.counts", new=AsyncMock(return_value={"parser-setup": 42})):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/downloads/counts")
    assert r.status_code == 200
    assert r.json()["counts"] == {"parser-setup": 42, "parser-portable": 0, "act-plugin": 0}


async def test_record_requires_auth(app):
    rec = AsyncMock(return_value=True)
    with patch(f"{_PATCH_BASE}.downloads_db.record_download", new=rec):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/downloads/parser-setup")
    assert r.status_code == 401
    rec.assert_not_awaited()


async def test_record_unknown_slug_404(app):
    """An unknown slug is rejected before anything is written — a client can't
    spam arbitrary keys into the table."""
    rec = AsyncMock(return_value=True)
    with patch(f"{_PATCH_BASE}.downloads_db.record_download", new=rec):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/downloads/not-a-real-slug", cookies=_cookies(_USER))
    assert r.status_code == 404
    rec.assert_not_awaited()


async def test_record_writes_and_returns_fresh_counts(app):
    rec = AsyncMock(return_value=True)
    counts = AsyncMock(return_value={"parser-setup": 1})
    with (
        patch(f"{_PATCH_BASE}.downloads_db.record_download", new=rec),
        patch(f"{_PATCH_BASE}.downloads_db.counts", new=counts),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/downloads/parser-setup", cookies=_cookies(_USER))
    assert r.status_code == 200
    rec.assert_awaited_once_with("disc-1", "parser-setup")
    assert r.json()["counts"]["parser-setup"] == 1
