"""GET /api/guild/{name}/history — store-only daily rows for the History tab,
plus the snapshot reducer the guild refresh feeds it from."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.census import store as census_store
from backend.census.config import WORLD as _WORLD
from backend.server.cache import guild_cache
from backend.server.core.cache_keys import guild_history_key
from backend.server.guild_cache import _guild_history_snapshot

_DAY = 86400


@pytest.fixture
def history_db(census_schema):
    """Leased scratch census schema (the shared store is already re-pointed
    by ``census_schema``) with a cold guild-history cache key."""
    guild_cache.delete(guild_history_key("Exordium", _WORLD))
    return census_schema


def _seed(schema, name: str, now: int, days_ago: list[int]) -> None:
    conn = census_store.CensusStore(schema).init_db()
    try:
        for d in days_ago:
            census_store.CensusStore.upsert_guild_history(
                conn,
                name,
                _WORLD,
                {"level": 100 + d, "members": 50, "accounts": 40, "achievement_count": 7},
                now=now - d * _DAY,
                retention_days=400,
            )
    finally:
        conn.close()


async def _get(app, name: str, query: str = "") -> tuple[int, dict]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(f"/api/guild/{name}/history{query}")
    return r.status_code, r.json()


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


async def test_unknown_guild_is_an_empty_list_not_a_404(app, history_db):
    status, body = await _get(app, "Exordium")
    assert status == 200
    assert body == {"guild": "Exordium", "world": _WORLD, "days": 90, "points": []}


async def test_points_are_sliced_to_the_requested_range_oldest_first(app, history_db):
    now = 1_800_000_000
    _seed(history_db, "Exordium", now, [200, 40, 5, 0])
    with patch("backend.server.api.guild.time.time", return_value=now):
        status, body = await _get(app, "Exordium", "?days=30")
    assert status == 200
    assert body["days"] == 30
    assert [p["level"] for p in body["points"]] == [105, 100]
    assert body["points"][0]["members"] == 50
    assert set(body["points"][0]) == {
        "day",
        "captured_at",
        "level",
        "members",
        "accounts",
        "achievement_count",
        "max_level_members",
        "distinct_classes",
    }
    with patch("backend.server.api.guild.time.time", return_value=now):
        _, wide = await _get(app, "Exordium", "?days=365")
    assert [p["level"] for p in wide["points"]] == [300, 140, 105, 100]


@pytest.mark.parametrize("bad", ["0", "401", "-1", "abc"])
async def test_days_outside_the_retention_window_is_422(app, history_db, bad):
    status, _ = await _get(app, "Exordium", f"?days={bad}")
    assert status == 422


async def test_history_never_calls_census(app, history_db):
    _seed(history_db, "Exordium", 1_800_000_000, [1])
    mock_client = AsyncMock()
    with patch("backend.server.core.census_lifecycle.CensusClient", return_value=mock_client):
        status, _ = await _get(app, "Exordium")
    assert status == 200
    mock_client.get_guild_full.assert_not_called()
    mock_client.get_guild.assert_not_called()


async def test_cached_rows_are_dropped_when_a_refresh_writes(app, history_db):
    """The route caches the full window per guild; the refresh path's write
    deletes that key, so the next read sees the new row without a restart."""
    now = 1_800_000_000
    _seed(history_db, "Exordium", now, [3])
    with patch("backend.server.api.guild.time.time", return_value=now):
        _, first = await _get(app, "Exordium")
    assert len(first["points"]) == 1
    assert guild_cache.get(guild_history_key("Exordium", _WORLD)) is not None

    _seed(history_db, "Exordium", now, [0])
    with patch("backend.server.api.guild.time.time", return_value=now):
        _, stale = await _get(app, "Exordium")
    assert len(stale["points"]) == 1  # served from cache — refresh hasn't invalidated yet

    guild_cache.delete(guild_history_key("Exordium", _WORLD))  # what _persist_and_publish_guild does
    with patch("backend.server.api.guild.time.time", return_value=now):
        _, fresh = await _get(app, "Exordium")
    assert len(fresh["points"]) == 2


async def test_malformed_guild_name_is_400(app, history_db):
    status, _ = await _get(app, "x" * 200)
    assert status == 400


# ---------------------------------------------------------------------------
# Snapshot reducer
# ---------------------------------------------------------------------------


def test_snapshot_reduces_info_and_roster():
    info = {"level": 300, "members": 42, "accounts": 30, "achievement_count": 9}
    members = [
        {"name": "A", "level": 80, "cls": "Templar"},
        {"name": "B", "level": 80, "cls": "Fury"},
        {"name": "C", "level": 79, "cls": "Templar"},
        {"name": "D", "level": None, "cls": None},
    ]
    assert _guild_history_snapshot(info, members, 80) == {
        "level": 300,
        "members": 42,
        "accounts": 30,
        "achievement_count": 9,
        "max_level_members": 2,
        "distinct_classes": 2,
    }


def test_snapshot_without_info_or_max_level_is_honest_about_unknowns():
    members = [{"name": "A", "level": 80, "cls": "Templar"}]
    snap = _guild_history_snapshot(None, members, None)
    assert snap["members"] == 1  # roster length when Census sent no count
    assert snap["level"] is None and snap["accounts"] is None and snap["achievement_count"] is None
    assert snap["max_level_members"] is None  # no registry row → no cap to count against
    assert snap["distinct_classes"] == 1
