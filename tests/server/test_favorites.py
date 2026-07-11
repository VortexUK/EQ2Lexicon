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
from backend.server.db import init_db, review_claim, submit_claim
from backend.server.db.favorites import store as fav
from tests.fixtures.users_db import point_users_db_at

_TEST_SECRET = "pytest-session-secret-not-real-0123456789"

_CAP = 50


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


@pytest.fixture(autouse=True)
def _stores_at_tmp(users_db: Path, monkeypatch: pytest.MonkeyPatch):
    """Point users.db (constant + every domain store) at this test's temp DB."""
    point_users_db_at(monkeypatch, users_db)


async def test_add_remove_round_trip(users_db):
    assert await fav.add_favorite("disc1", "Menludiir", "Varsoon", cap=_CAP) is True
    assert await fav.count_favorites_for_character("Menludiir", "Varsoon") == 1
    assert await fav.is_favorited("disc1", "Menludiir", "Varsoon") is True
    assert await fav.remove_favorite("disc1", "Menludiir", "Varsoon") is True
    assert await fav.count_favorites_for_character("Menludiir", "Varsoon") == 0
    assert await fav.is_favorited("disc1", "Menludiir", "Varsoon") is False


async def test_add_is_idempotent(users_db):
    assert await fav.add_favorite("disc1", "Menludiir", "Varsoon", cap=_CAP) is True
    assert await fav.add_favorite("disc1", "Menludiir", "Varsoon", cap=_CAP) is False
    assert await fav.count_favorites_for_character("Menludiir", "Varsoon") == 1


async def test_add_enforces_cap_atomically(users_db):
    """The cap guard lives inside the INSERT itself, so it can't be raced."""
    assert await fav.add_favorite("disc1", "Alpha", "Varsoon", cap=2) is True
    assert await fav.add_favorite("disc1", "Bravo", "Varsoon", cap=2) is True
    assert await fav.add_favorite("disc1", "Charlie", "Varsoon", cap=2) is False  # cap hit
    assert await fav.is_favorited("disc1", "Charlie", "Varsoon") is False
    # Cap is per-world: the same user can still favourite on another world.
    assert await fav.add_favorite("disc1", "Charlie", "Wuoshi", cap=2) is True


async def test_remove_missing_is_noop(users_db):
    assert await fav.remove_favorite("disc1", "Ghost", "Varsoon") is False


async def test_count_across_users(users_db):
    await fav.add_favorite("disc1", "Menludiir", "Varsoon", cap=_CAP)
    await fav.add_favorite("disc2", "Menludiir", "Varsoon", cap=_CAP)
    assert await fav.count_favorites_for_character("Menludiir", "Varsoon") == 2
    assert await fav.is_favorited("disc3", "Menludiir", "Varsoon") is False


async def test_world_scoping(users_db):
    """The same character name on two worlds is two independent favourites."""
    await fav.add_favorite("disc1", "Menludiir", "Varsoon", cap=_CAP)
    await fav.add_favorite("disc1", "Menludiir", "Wuoshi", cap=_CAP)
    assert await fav.count_favorites_for_character("Menludiir", "Varsoon") == 1
    assert await fav.count_user_favorites("disc1", "Varsoon") == 1
    assert await fav.count_user_favorites("disc1", "Wuoshi") == 1
    varsoon = await fav.list_favorites("disc1", "Varsoon")
    assert [r["character_name"] for r in varsoon] == ["Menludiir"]


async def test_list_newest_first(users_db):
    import aiosqlite

    await fav.add_favorite("disc1", "Alpha", "Varsoon", cap=_CAP)
    await fav.add_favorite("disc1", "Bravo", "Varsoon", cap=_CAP)
    # Force distinct created_at so ordering is deterministic.
    async with aiosqlite.connect(users_db) as db:
        await db.execute("UPDATE character_favorites SET created_at = 100 WHERE character_name = 'Alpha'")
        await db.execute("UPDATE character_favorites SET created_at = 200 WHERE character_name = 'Bravo'")
        await db.commit()
    rows = await fav.list_favorites("disc1", "Varsoon")
    assert [r["character_name"] for r in rows] == ["Bravo", "Alpha"]


async def test_claim_approval_removes_own_favorite(users_db):
    """When a claim is approved the new owner's favourite of that character is
    removed (case-insensitively) in the same transaction — you can't favourite
    your own character. Other users' favourites are untouched."""
    await fav.add_favorite("disc1", "Menludiir", "Varsoon", cap=_CAP)
    await fav.add_favorite("disc2", "Menludiir", "Varsoon", cap=_CAP)
    claim = await submit_claim("disc1", "menludiir", "Varsoon")  # different casing
    await review_claim(claim["id"], "approved", "admin-1")
    assert await fav.is_favorited("disc1", "Menludiir", "Varsoon") is False  # removed
    assert await fav.is_favorited("disc2", "Menludiir", "Varsoon") is True  # untouched


async def test_claim_rejection_keeps_favorite(users_db):
    await fav.add_favorite("disc1", "Menludiir", "Varsoon", cap=_CAP)
    claim = await submit_claim("disc1", "Menludiir", "Varsoon")
    await review_claim(claim["id"], "rejected", "admin-1")
    assert await fav.is_favorited("disc1", "Menludiir", "Varsoon") is True


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
    existing character with zero favourites, not owned by the caller."""
    defaults = {
        "count_favorites_for_character": AsyncMock(return_value=0),
        "is_favorited": AsyncMock(return_value=False),
        "add_favorite": AsyncMock(return_value=True),
        "remove_favorite": AsyncMock(return_value=True),
        "list_favorites": AsyncMock(return_value=[]),
    }
    defaults.update(overrides)
    return patch.multiple(f"{_PATCH_BASE}.favorites_db", **defaults), defaults


async def _request(app, method: str, url: str, *, cookies=True, exists=True, own=False, **db_overrides):
    patcher, mocks = _db_patches(**db_overrides)
    with (
        patcher,
        patch(f"{_PATCH_BASE}._character_exists", new=AsyncMock(return_value=exists)),
        patch(f"{_PATCH_BASE}._is_own_character", new=AsyncMock(return_value=own)),
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


async def test_put_own_character_400(app):
    """You can't favourite your own (approved-claim) character."""
    r, mocks = await _request(app, "PUT", "/api/character/Menludiir/favorite", own=True)
    assert r.status_code == 400
    assert "own character" in r.json()["detail"]
    mocks["add_favorite"].assert_not_awaited()


async def test_put_cap_409(app):
    """Insert blocked by the in-SQL cap (rowcount 0, row still absent) → 409."""
    r, mocks = await _request(
        app,
        "PUT",
        "/api/character/Menludiir/favorite",
        add_favorite=AsyncMock(return_value=False),
        is_favorited=AsyncMock(return_value=False),
    )
    assert r.status_code == 409
    assert "limit" in r.json()["detail"].lower()


async def test_put_already_favorited_is_idempotent(app):
    """PUT on an existing favourite short-circuits — no insert attempted."""
    r, mocks = await _request(
        app,
        "PUT",
        "/api/character/Menludiir/favorite",
        is_favorited=AsyncMock(return_value=True),
        count_favorites_for_character=AsyncMock(return_value=1),
    )
    assert r.status_code == 200
    assert r.json()["favorited_by_me"] is True
    mocks["add_favorite"].assert_not_awaited()


async def test_get_primes_and_serves_count_cache(app):
    """First GET caches the count; a second GET serves the cached value WITHOUT
    re-running the count query (the whole point of the cache)."""
    key = "favcount:menludiir:varsoon"
    r, mocks = await _request(app, "GET", "/api/character/Menludiir/favorite", cookies=False)
    assert r.status_code == 200 and favorite_count_cache.get(key) == 0
    mocks["count_favorites_for_character"].assert_awaited_once()
    r, mocks = await _request(
        app,
        "GET",
        "/api/character/Menludiir/favorite",
        cookies=False,
        count_favorites_for_character=AsyncMock(return_value=5),
    )
    assert r.json()["count"] == 0  # cached value wins…
    mocks["count_favorites_for_character"].assert_not_awaited()  # …and no query ran


async def test_count_cache_invalidated_on_write(app):
    """A PUT drops the cached count, so its own response reflects the fresh
    post-insert count instead of the stale cached zero."""
    key = "favcount:menludiir:varsoon"
    favorite_count_cache.set(key, 0)  # stale pre-primed count
    r, _ = await _request(
        app,
        "PUT",
        "/api/character/Menludiir/favorite",
        count_favorites_for_character=AsyncMock(return_value=1),
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
