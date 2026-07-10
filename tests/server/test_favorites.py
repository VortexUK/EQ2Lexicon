"""Character-favourites DB layer + API tests.

DB layer is exercised against a temp users.db (explicit ``path=``) — this also
proves the new schema block passes ``_assertions.py`` via ``init_db``. The API
is tested with the ``app`` fixture: a signed session cookie for auth + mocked
db helpers (same pattern as test_raid_schedule.py).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import itsdangerous
import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.cache import favorite_count_cache
from backend.server.db import favorites as fav
from backend.server.db import init_db

_TEST_SECRET = "pytest-session-secret-not-real-0123456789"


@pytest.fixture(autouse=True)
def _clear_count_cache():
    favorite_count_cache._store.clear()
    yield
    favorite_count_cache._store.clear()


# ---------------------------------------------------------------------------
# DB layer (temp users.db)
# ---------------------------------------------------------------------------


@pytest.fixture
def users_db(tmp_path) -> Path:
    db = tmp_path / "users.db"
    init_db(db)  # creates character_favorites (+ asserts schema completeness)
    return db


async def test_add_remove_round_trip(users_db):
    assert await fav.add_favorite("disc1", "Menludiir", "Varsoon", path=users_db) is True
    status = await fav.get_favorite_status("Menludiir", "Varsoon", "disc1", path=users_db)
    assert status == {"count": 1, "favorited_by_me": True}
    assert await fav.remove_favorite("disc1", "Menludiir", "Varsoon", path=users_db) is True
    status = await fav.get_favorite_status("Menludiir", "Varsoon", "disc1", path=users_db)
    assert status == {"count": 0, "favorited_by_me": False}


async def test_add_is_idempotent(users_db):
    assert await fav.add_favorite("disc1", "Menludiir", "Varsoon", path=users_db) is True
    assert await fav.add_favorite("disc1", "Menludiir", "Varsoon", path=users_db) is False  # already there
    status = await fav.get_favorite_status("Menludiir", "Varsoon", "disc1", path=users_db)
    assert status["count"] == 1  # not double-counted


async def test_remove_missing_is_noop(users_db):
    assert await fav.remove_favorite("disc1", "Ghost", "Varsoon", path=users_db) is False


async def test_count_across_users_and_anonymous(users_db):
    await fav.add_favorite("disc1", "Menludiir", "Varsoon", path=users_db)
    await fav.add_favorite("disc2", "Menludiir", "Varsoon", path=users_db)
    status = await fav.get_favorite_status("Menludiir", "Varsoon", "disc3", path=users_db)
    assert status == {"count": 2, "favorited_by_me": False}
    anon = await fav.get_favorite_status("Menludiir", "Varsoon", None, path=users_db)
    assert anon == {"count": 2, "favorited_by_me": False}


async def test_world_scoping(users_db):
    """The same character name on two worlds is two independent favourites."""
    await fav.add_favorite("disc1", "Menludiir", "Varsoon", path=users_db)
    await fav.add_favorite("disc1", "Menludiir", "Wuoshi", path=users_db)
    assert (await fav.get_favorite_status("Menludiir", "Varsoon", None, path=users_db))["count"] == 1
    assert await fav.count_user_favorites("disc1", "Varsoon", path=users_db) == 1
    assert await fav.count_user_favorites("disc1", "Wuoshi", path=users_db) == 1
    varsoon = await fav.list_favorites("disc1", "Varsoon", path=users_db)
    assert [r["character_name"] for r in varsoon] == ["Menludiir"]


async def test_list_newest_first(users_db):
    import aiosqlite

    await fav.add_favorite("disc1", "Alpha", "Varsoon", path=users_db)
    await fav.add_favorite("disc1", "Bravo", "Varsoon", path=users_db)
    # Force distinct created_at so ordering is deterministic.
    async with aiosqlite.connect(users_db) as db:
        await db.execute("UPDATE character_favorites SET created_at = 100 WHERE character_name = 'Alpha'")
        await db.execute("UPDATE character_favorites SET created_at = 200 WHERE character_name = 'Bravo'")
        await db.commit()
    rows = await fav.list_favorites("disc1", "Varsoon", path=users_db)
    assert [r["character_name"] for r in rows] == ["Bravo", "Alpha"]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _cookies(user: dict) -> dict:
    payload = base64.b64encode(json.dumps({"user": user}).encode()).decode()
    signed = itsdangerous.TimestampSigner(_TEST_SECRET).sign(payload).decode()
    return {"session": signed}


_USER = {"id": "disc-1", "username": "tester"}

_PATCH_BASE = "backend.server.api.favorites"


def _db_patches(**overrides):
    """Patch every favorites_db helper the routes touch. Defaults model an
    existing character with zero favourites."""
    defaults = {
        "get_favorite_status": AsyncMock(return_value={"count": 0, "favorited_by_me": False}),
        "count_user_favorites": AsyncMock(return_value=0),
        "add_favorite": AsyncMock(return_value=True),
        "remove_favorite": AsyncMock(return_value=True),
        "list_favorites": AsyncMock(return_value=[]),
    }
    defaults.update(overrides)
    return patch.multiple(f"{_PATCH_BASE}.favorites_db", **defaults), defaults


async def _request(app, method: str, url: str, *, cookies=True, exists=True, **db_overrides):
    patcher, mocks = _db_patches(**db_overrides)
    with (
        patcher,
        patch(f"{_PATCH_BASE}._character_exists", new=AsyncMock(return_value=exists)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.request(method, url, cookies=_cookies(_USER) if cookies else {})
    return r, mocks


async def test_get_status_is_public(app):
    r, _ = await _request(app, "GET", "/api/character/Menludiir/favorite", cookies=False)
    assert r.status_code == 200
    assert r.json() == {"count": 0, "favorited_by_me": False}


async def test_put_requires_auth(app):
    r, _ = await _request(app, "PUT", "/api/character/Menludiir/favorite", cookies=False)
    assert r.status_code == 401


async def test_delete_requires_auth(app):
    r, _ = await _request(app, "DELETE", "/api/character/Menludiir/favorite", cookies=False)
    assert r.status_code == 401


async def test_list_requires_auth(app):
    r, _ = await _request(app, "GET", "/api/favorites", cookies=False)
    assert r.status_code == 401


async def test_put_round_trip_capitalises(app):
    """A lowercase URL name reaches the DB capitalised."""
    r, mocks = await _request(app, "PUT", "/api/character/menludiir/favorite")
    assert r.status_code == 200
    mocks["add_favorite"].assert_awaited_once()
    assert mocks["add_favorite"].await_args.args[1] == "Menludiir"


async def test_put_invalid_name_400(app):
    r, mocks = await _request(app, "PUT", "/api/character/bad--name123/favorite")
    assert r.status_code == 400
    mocks["add_favorite"].assert_not_awaited()


async def test_put_unknown_character_404(app):
    r, mocks = await _request(app, "PUT", "/api/character/Ghost/favorite", exists=False)
    assert r.status_code == 404
    mocks["add_favorite"].assert_not_awaited()


async def test_put_cap_409(app):
    r, mocks = await _request(
        app,
        "PUT",
        "/api/character/Menludiir/favorite",
        count_user_favorites=AsyncMock(return_value=50),
    )
    assert r.status_code == 409
    mocks["add_favorite"].assert_not_awaited()


async def test_put_already_favorited_skips_cap_and_insert(app):
    """PUT on an existing favourite is idempotent — no cap check bite, no insert."""
    r, mocks = await _request(
        app,
        "PUT",
        "/api/character/Menludiir/favorite",
        get_favorite_status=AsyncMock(return_value={"count": 1, "favorited_by_me": True}),
        count_user_favorites=AsyncMock(return_value=50),  # would 409 if consulted
    )
    assert r.status_code == 200
    mocks["add_favorite"].assert_not_awaited()


async def test_get_primes_and_serves_count_cache(app):
    """First GET caches the count; a later GET serves the cached value even if
    the DB has moved on (TTL/invalidations own freshness)."""
    key = "favcount:menludiir:varsoon"
    r, _ = await _request(app, "GET", "/api/character/Menludiir/favorite", cookies=False)
    assert r.status_code == 200 and favorite_count_cache.get(key) == 0
    r, _ = await _request(
        app,
        "GET",
        "/api/character/Menludiir/favorite",
        cookies=False,
        get_favorite_status=AsyncMock(return_value={"count": 5, "favorited_by_me": False}),
    )
    assert r.json()["count"] == 0  # cached value wins until invalidated


async def test_count_cache_invalidated_on_write(app):
    """A PUT drops the cached count, so its own response reflects the fresh
    post-insert count instead of the stale cached zero."""
    key = "favcount:menludiir:varsoon"
    favorite_count_cache.set(key, 0)  # stale pre-primed count
    r, _ = await _request(
        app,
        "PUT",
        "/api/character/Menludiir/favorite",
        # Both status reads in the PUT see the post-insert DB state.
        get_favorite_status=AsyncMock(return_value={"count": 1, "favorited_by_me": False}),
    )
    assert r.status_code == 200
    assert r.json()["count"] == 1  # would be 0 if the stale key survived


async def test_list_enrichment_fallback(app):
    """One favourite enriched from the store, one name-only."""
    rows = [
        {"character_name": "Menludiir", "world": "Varsoon", "created_at": 200},
        {"character_name": "Ghostly", "world": "Varsoon", "created_at": 100},
    ]
    with (
        patch(f"{_PATCH_BASE}.favorites_db.list_favorites", new=AsyncMock(return_value=rows)),
        patch(
            f"{_PATCH_BASE}._store_character_data_many",
            return_value={"Menludiir": {"level": 70, "cls": "Templar", "guild_name": "Exordium"}},
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/favorites", cookies=_cookies(_USER))
    assert r.status_code == 200
    favs = r.json()["favorites"]
    assert favs[0]["character_name"] == "Menludiir"
    assert favs[0]["level"] == 70
    assert favs[0]["guild_name"] == "Exordium"
    assert favs[1]["character_name"] == "Ghostly"
    assert favs[1]["level"] is None  # name-only fallback
