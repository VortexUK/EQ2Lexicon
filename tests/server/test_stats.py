"""Tests for the server statistics API (census character.stat family).

The census client is mocked at the census_lifecycle layer (same pattern as
test_gear_sets); classes.db and spells.db assertions run against the real
committed catalogues so classid→name and crc→ability mappings stay honest.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server.api import stats as stats_mod
from backend.server.cache import lifetime_cache
from tests.fixtures.users import make_fake_require_user, make_fake_user

_fake_user = make_fake_require_user(make_fake_user(id="123"))

WORLDID = 618


def _bucket(count: int, kills_avg: float = 5000.0, kills_max: float = 100000.0) -> dict:
    return {
        "count": count,
        "ts": 1784972773,
        "kills": {"max": kills_max, "sum": kills_avg * count, "avg": kills_avg},
        "deaths": {"max": 3000, "sum": 30_000, "avg": 30.0},
        "kills_deaths_ratio": {"max": 20000, "sum": 5000, "avg": 160.0},
        "max_melee_hit": {"max": 268591, "sum": 1, "avg": 4000.0},
        "max_magic_hit": {"max": 12929854, "sum": 1, "avg": 9000.0},
        "quests.complete": {"max": 2221, "sum": 1_358_803, "avg": 63.7},
        "collections.complete": {"max": 500, "sum": 78_657, "avg": 3.7},
        "achievements.points": {"max": 2605, "sum": 100, "avg": 331.4},
        "items_crafted": {"max": 4_978_221, "sum": 40_225_247, "avg": 1884.7},
        "rare_harvests": {"max": 18780, "sum": 891_495, "avg": 41.8},
    }


def _char(name: str, cls: str, stat: str, value: float) -> dict:
    return {"name": {"first": name}, "type": {"class": cls, "level": 70}, "statistics": {stat: {"value": value}}}


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.get_worldid = AsyncMock(return_value=WORLDID)
    client.get_stat_aggregates = AsyncMock(
        return_value={
            "global": [
                {"id": "all", "value": _bucket(500_000, kills_avg=9000)},
                {"id": "13", "value": _bucket(20_000, kills_avg=7000)},  # Templar, game-wide
            ],
            "world": [{"id": str(WORLDID), "value": _bucket(21_343, kills_avg=6843.5, kills_max=465_696)}],
            "world_class": [
                {"id": f"{WORLDID}.13", "value": _bucket(900, kills_avg=6000)},  # Templar
                {"id": f"{WORLDID}.19", "value": _bucket(1100, kills_avg=8000)},  # Mystic
                {"id": "999.13", "value": _bucket(5)},  # other world — must be ignored
            ],
        }
    )
    client.get_stat_leaders = AsyncMock(
        return_value=[_char("Kaipai", "Necromancer", "kills", 465_696), _char("Touxin", "Fury", "kills", 459_917)]
    )
    # Range mode returns UNSORTED rows — the builder must sort client-side.
    client.get_stat_range = AsyncMock(
        side_effect=lambda world, path, minimum, limit=200: [
            _char("Biffels", "Berserker", path.split(".")[1], 260_007),
            _char("Dema", "Guardian", path.split(".")[1], 268_591),
        ]
    )
    client.get_character_statistics = AsyncMock(return_value=None)
    return client


@pytest.fixture(autouse=True)
def _fresh_caches():
    stats_mod._stats_cache.clear()
    stats_mod._stats_locks.clear()
    stats_mod._last_build_failure.clear()
    stats_mod._explore_cache.clear()
    lifetime_cache._store.clear()
    yield
    stats_mod._stats_cache.clear()
    stats_mod._stats_locks.clear()
    stats_mod._last_build_failure.clear()
    stats_mod._explore_cache.clear()
    lifetime_cache._store.clear()


def _census(client: MagicMock):
    mock_cc = MagicMock(return_value=client)
    return (
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient", mock_cc),
    )


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_server_stats_shapes_payload():
    client = _mock_client()
    p1, p2 = _census(client)
    with p1, p2:
        stats = await stats_mod._build_server_stats("Wuoshi")

    assert stats is not None
    assert stats.population == 21_343
    assert stats.totals["kills"] == pytest.approx(6843.5 * 21_343)
    assert stats.records["max_melee_hit"] == 268_591

    # classid → name via the committed classes.db (13=Templar, 19=Mystic),
    # sorted by population desc; the other-world row is excluded.
    assert [(c.name, c.count) for c in stats.classes] == [("Mystic", 1100), ("Templar", 900)]
    assert stats.classes[1].global_avg["kills"] == 7000

    # Sorted boards come straight through; range boards are client-sorted.
    assert stats.leaders["kills"][0].name == "Kaipai"
    assert [e.name for e in stats.leaders["max_melee_hit"][:2]] == ["Dema", "Biffels"]


@pytest.mark.asyncio
async def test_build_returns_none_when_census_fails():
    client = _mock_client()
    client.get_stat_aggregates = AsyncMock(return_value=None)
    p1, p2 = _census(client)
    with p1, p2:
        assert await stats_mod._build_server_stats("Wuoshi") is None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_stats_endpoint_202_when_cold_then_serves(app):
    """A cold cache never builds inline: the endpoint kicks a background
    build and answers 202; once the cache is warm it serves 200 without
    re-fetching the aggregates."""
    client = _mock_client()
    p1, p2 = _census(client)
    with p1, p2, patch("backend.server.api.stats._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            cold = await http.get("/api/stats/server")
            assert cold.status_code == 202
            assert cold.json() == {"status": "building"}
            # Let the background build (kicked by the request) finish.
            await stats_mod._refresh_server_stats("Varsoon")
            warm = await http.get("/api/stats/server")
            again = await http.get("/api/stats/server")

    assert warm.status_code == 200
    assert warm.json()["population"] == 21_343
    assert again.status_code == 200
    # Aggregates fetched once (bg task + explicit refresh dedupe on the lock).
    assert client.get_stat_aggregates.await_count == 1


@pytest.mark.asyncio
async def test_server_stats_503_after_recent_failed_build(app):
    client = _mock_client()
    client.get_worldid = AsyncMock(return_value=None)
    p1, p2 = _census(client)
    with p1, p2, patch("backend.server.api.stats._require_user", _fake_user):
        # A failed build stamps the cooldown → the endpoint 503s instead of
        # keeping the frontend polling forever.
        await stats_mod._refresh_server_stats("Varsoon")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            r = await http.get("/api/stats/server")
    assert r.status_code == 503


_REAL_SPELLS_DB = __import__("pathlib").Path("data/spells/spells.db")


@pytest.mark.asyncio
@pytest.mark.skipif(not _REAL_SPELLS_DB.exists(), reason="local spells.db not present (gitignored artifact)")
async def test_character_lifetime_resolves_ability_names(app, monkeypatch):
    """crc→ability via the local spells.db: 1729734970 = Frenzy II
    (real-data assertion — the id came from a live census record hit).
    conftest points the spells catalogue at an empty test db; re-point it
    at the real artifact for this test only."""
    from backend.eq2db.spells import catalogue as spells_catalogue

    monkeypatch.setattr(spells_catalogue, "path", _REAL_SPELLS_DB)
    client = _mock_client()
    client.get_character_statistics = AsyncMock(
        return_value={
            "name": {"first": "Badbang"},
            "type": {"class": "Berserker"},
            "statistics": {
                "kills": {"value": 17286},
                "deaths": {"value": 140},
                "kills_deaths_ratio": {"value": 123.47142},
                "max_melee_hit": {"weapon": 1729734970, "value": 125009},
                "max_magic_hit": {"spell": 4294967295, "value": 66938},  # sentinel → unnamed
                "items_crafted": {"value": 2243},
                "rare_harvests": {"value": 0},
            },
        }
    )
    p1, p2 = _census(client)
    with p1, p2, patch("backend.server.api.stats._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            r = await http.get("/api/stats/character/Badbang")

    assert r.status_code == 200
    body = r.json()
    assert body["kills"] == 17286
    assert body["kills_deaths_ratio"] == 123.5
    assert body["max_melee_ability"] == "Frenzy II"
    assert body["max_magic_ability"] is None  # sentinel crc stays unnamed


@pytest.mark.asyncio
async def test_character_lifetime_404_and_400(app):
    client = _mock_client()
    p1, p2 = _census(client)
    with p1, p2, patch("backend.server.api.stats._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            missing = await http.get("/api/stats/character/Ghostchar")
            invalid = await http.get("/api/stats/character/bad name!!")
    assert missing.status_code == 404
    assert invalid.status_code == 400


# ---------------------------------------------------------------------------
# Explorer
# ---------------------------------------------------------------------------


def _combat_char(name: str, cls: str, ability_mod: float) -> dict:
    return {
        "name": {"first": name},
        "type": {"class": cls, "level": 70},
        "stats": {"combat": {"abilitymod": ability_mod}},
    }


@pytest.mark.asyncio
async def test_explore_rejects_unknown_stat_and_class(app):
    client = _mock_client()
    p1, p2 = _census(client)
    with p1, p2, patch("backend.server.api.stats._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            bad_stat = await http.get("/api/stats/explore?stat=hax")
            bad_cls = await http.get("/api/stats/explore?stat=ability_mod&cls=Wibble")
    assert bad_stat.status_code == 400
    assert bad_cls.status_code == 400
    client.get_stat_leaders.assert_not_awaited()


@pytest.mark.asyncio
async def test_explore_class_filter_and_client_sort(app):
    """cls=Templar must reach census as type.classid=13 (string names are
    silently ignored by census), combat stats query the `stats` subtree, and
    rows are re-sorted client-side."""
    client = _mock_client()
    client.get_stat_leaders = AsyncMock(
        return_value=[_combat_char("Lowmod", "Templar", 900), _combat_char("Menludiir", "Templar", 1868)]
    )
    p1, p2 = _census(client)
    with p1, p2, patch("backend.server.api.stats._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            r = await http.get("/api/stats/explore?stat=ability_mod&cls=Templar")

    assert r.status_code == 200
    body = r.json()
    assert [e["name"] for e in body["entries"]] == ["Menludiir", "Lowmod"]
    assert body["entries"][0]["value"] == 1868

    args, kwargs = client.get_stat_leaders.await_args
    assert args[1] == "stats.combat.abilitymod"
    assert args[3] == {"type.classid": "13"}  # committed classes.db: Templar icon_id 13
    assert kwargs["show"] == "stats"


@pytest.mark.asyncio
async def test_explore_progression_stat_uses_narrow_projection(app):
    """Progression stats live at top-level scalar paths (quests.complete,
    achievements.points, alternateadvancements.spentpoints) and must project
    the exact scalar — c:show=achievements would drag the 500+-item
    achievement_list into every row."""
    client = _mock_client()
    client.get_stat_leaders = AsyncMock(
        return_value=[
            {"name": {"first": "Talormane"}, "type": {"class": "Warden", "level": 70}, "quests": {"complete": 2232}}
        ]
    )
    p1, p2 = _census(client)
    with p1, p2, patch("backend.server.api.stats._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            r = await http.get("/api/stats/explore?stat=quests_completed")

    assert r.status_code == 200
    assert r.json()["entries"][0] == {"name": "Talormane", "cls": "Warden", "level": 70, "value": 2232}
    args, kwargs = client.get_stat_leaders.await_args
    assert args[1] == "quests.complete"
    assert kwargs["show"] == "quests.complete"


@pytest.mark.asyncio
async def test_explore_kd_floor_and_statistics_show(app):
    client = _mock_client()
    client.get_stat_leaders = AsyncMock(return_value=[_char("Badbang", "Berserker", "kills_deaths_ratio", 123.5)])
    p1, p2 = _census(client)
    with p1, p2, patch("backend.server.api.stats._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            r = await http.get("/api/stats/explore?stat=kd_ratio")

    assert r.status_code == 200
    args, kwargs = client.get_stat_leaders.await_args
    assert args[3] == {"statistics.kills.value": f"]{stats_mod.KD_KILLS_FLOOR}"}
    assert kwargs["show"] == "statistics"


@pytest.mark.asyncio
async def test_explore_cache_hit_skips_census(app):
    client = _mock_client()
    client.get_stat_leaders = AsyncMock(return_value=[_combat_char("Menludiir", "Templar", 1868)])
    p1, p2 = _census(client)
    with p1, p2, patch("backend.server.api.stats._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            first = await http.get("/api/stats/explore?stat=ability_mod&cls=Templar")
            second = await http.get("/api/stats/explore?stat=ability_mod&cls=Templar")

    assert first.status_code == second.status_code == 200
    assert client.get_stat_leaders.await_count == 1


@pytest.mark.asyncio
async def test_explore_stale_beats_error_and_503_when_cold(app):
    client = _mock_client()
    client.get_stat_leaders = AsyncMock(return_value=[_combat_char("Menludiir", "Templar", 1868)])
    p1, p2 = _census(client)
    with p1, p2, patch("backend.server.api.stats._require_user", _fake_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            warm = await http.get("/api/stats/explore?stat=ability_mod")
            assert warm.status_code == 200

            # Expire the cache entry, then break census: stale data still serves.
            key, (_, payload) = next(iter(stats_mod._explore_cache.items()))
            stats_mod._explore_cache[key] = (-(10**9), payload)
            client.get_stat_leaders = AsyncMock(return_value=None)
            stale = await http.get("/api/stats/explore?stat=ability_mod")
            assert stale.status_code == 200
            assert stale.json()["entries"][0]["name"] == "Menludiir"

            # No cache at all + census down → 503.
            stats_mod._explore_cache.clear()
            cold = await http.get("/api/stats/explore?stat=ability_mod")
            assert cold.status_code == 503
