"""Tests for the zones route — list + detail endpoints.

DB access is mocked (matches the codebase convention) so the suite passes
without a built ``data/zones/zones.db``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


def _fake_zone(name: str = "The Emerald Halls", with_bosses: bool = True) -> dict:
    """Hydrated-zone shape that ``zones_db.find_by_name`` returns."""
    return {
        "id": 1,
        "name": name,
        "name_lower": name.lower(),
        "expansion_short": "EoF",
        "expansion_name": "Echoes of Faydwer",
        "expansion_year": 2006,
        "expansion_confidence": "category",
        "expansion_source": "",
        "is_persistent_instance": False,
        "is_endless_persistent": False,
        "is_tradeskill": False,
        "is_pvp": False,
        "is_openworld": False,
        "is_instance": True,
        "is_live_event": False,
        "is_city": False,
        "is_contested": False,
        "is_deprecated": False,
        "event_name": None,
        "wiki_url": "https://eq2.fandom.com/wiki/The_Emerald_Halls",
        "types": ["raid_x4"],
        "aliases": [],
        "bosses": (
            [
                {
                    "id": 1,
                    "encounter_name": "Prince Thirneg",
                    "position": 1,
                    "stage": "First Floor",
                    "wiki_url": None,
                    "mobs": [{"id": 1, "mob_name": "Prince Thirneg", "position": 0}],
                },
                {
                    "id": 2,
                    "encounter_name": "Wuoshi",
                    "position": 13,
                    "stage": "Third Floor",
                    "wiki_url": None,
                    "mobs": [{"id": 2, "mob_name": "Wuoshi", "position": 0}],
                },
            ]
            if with_bosses
            else []
        ),
    }


# ---------------------------------------------------------------------------
# GET /api/zones/{name}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_zone_returns_hydrated_payload(app):
    with patch("backend.server.api.zones.zones_db.find_by_name", return_value=_fake_zone()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/The Emerald Halls")

    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "The Emerald Halls"
    assert data["expansion_short"] == "EoF"
    assert data["types"] == ["raid_x4"]
    assert len(data["bosses"]) == 2
    first = data["bosses"][0]
    assert first["encounter_name"] == "Prince Thirneg"
    assert first["stage"] == "First Floor"
    assert first["mobs"] == [{"id": 1, "mob_name": "Prince Thirneg", "position": 0}]


@pytest.mark.asyncio
async def test_get_zone_unknown_name_returns_404(app):
    with patch("backend.server.api.zones.zones_db.find_by_name", return_value=None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones/Made Up Place")

    assert r.status_code == 404
    assert "Made Up Place" in r.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/zones (list)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_zones_by_expansion_uses_type_default(app):
    """Default filter is type=raid_x4 — list_by_expansion is called with it."""
    with patch(
        "backend.server.api.zones.zones_db.list_by_expansion",
        return_value=[_fake_zone("The Emerald Halls"), _fake_zone("Veeshan's Peak", with_bosses=False)],
    ) as m:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones?expansion=EoF")

    assert r.status_code == 200
    m.assert_called_once_with("EoF", "raid_x4")
    data = r.json()
    assert data["expansion"] == "EoF"
    assert data["type"] == "raid_x4"
    assert [z["name"] for z in data["zones"]] == ["The Emerald Halls", "Veeshan's Peak"]


@pytest.mark.asyncio
async def test_list_zones_empty_type_disables_type_filter(app):
    """type= (empty) means list_by_expansion is called with type=None."""
    with patch("backend.server.api.zones.zones_db.list_by_expansion", return_value=[]) as m:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones?expansion=EoF&type=")

    assert r.status_code == 200
    m.assert_called_once_with("EoF", None)


@pytest.mark.asyncio
async def test_list_zones_by_type_only(app):
    """No expansion + type=raid_x4 routes through list_by_type."""
    with patch(
        "backend.server.api.zones.zones_db.list_by_type",
        return_value=[_fake_zone()],
    ) as m:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/zones?type=raid_x4")

    assert r.status_code == 200
    m.assert_called_once_with("raid_x4")


@pytest.mark.asyncio
async def test_list_zones_no_filters_is_400(app):
    """Refuse the unbounded query rather than returning the entire 1k+ zone table."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/zones?type=")

    assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/zones/progress
# ---------------------------------------------------------------------------


def _signed_in_client(app):
    """Helper that attaches a fake session user so require_user_session passes."""
    # The session middleware reads from request.session — easiest path under
    # ASGITransport is to override the require_user_session dependency directly.
    from backend.server.auth_deps import require_user_session

    app.dependency_overrides[require_user_session] = lambda: {"id": "discord-123", "username": "tester"}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_get_progress_requires_session(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/zones/progress")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_progress_returns_empty_when_no_guild(app):
    """No primary character + no recent parses → guild_name None, empty progress."""
    with (
        patch(
            "backend.server.core.primary_guild.get_active_claims",
            new_callable=AsyncMock,
            return_value={"approved": [], "pending": None},
        ),
        patch("backend.server.api.zones._most_recent_parsed_guild_sync", return_value=None),
    ):
        async with _signed_in_client(app) as client:
            r = await client.get("/api/zones/progress")

    assert r.status_code == 200
    data = r.json()
    assert data["guild_name"] is None
    assert data["killed_encounters"] == {}


@pytest.mark.asyncio
async def test_get_progress_aggregates_kills(app):
    """Primary character resolves to a guild; killed encounters group by zone
    with per-encounter kill metadata (count + last kill id + timestamp)."""
    fake_progress = {
        "The Emerald Halls": [
            {"encounter_name": "Prince Thirneg", "kill_count": 3, "last_kill_id": 101, "last_kill_at": 1716000000},
            {"encounter_name": "Wuoshi", "kill_count": 1, "last_kill_id": 202, "last_kill_at": 1716100000},
        ],
        "Veeshan's Peak": [
            {"encounter_name": "Druushk", "kill_count": 2, "last_kill_id": 303, "last_kill_at": 1716200000},
        ],
    }
    with (
        patch(
            "backend.server.core.primary_guild.get_active_claims",
            new_callable=AsyncMock,
            return_value={
                "approved": [{"character_name": "Sihtric", "is_primary": 1}],
                "pending": None,
            },
        ),
        # Cache miss forces the most-recent-parses fallback path.
        patch("backend.server.core.primary_guild.character_cache.get_stale", return_value=(None, False)),
        patch("backend.server.api.zones._most_recent_parsed_guild_sync", return_value="Exordium"),
        patch("backend.server.api.zones._compute_progress_sync", return_value=fake_progress),
    ):
        async with _signed_in_client(app) as client:
            r = await client.get("/api/zones/progress")

    assert r.status_code == 200
    data = r.json()
    assert data["guild_name"] == "Exordium"
    assert data["character_name"] == "Sihtric"
    eh = data["killed_encounters"]["The Emerald Halls"]
    assert [e["encounter_name"] for e in eh] == ["Prince Thirneg", "Wuoshi"]
    assert eh[0]["kill_count"] == 3
    assert eh[0]["last_kill_id"] == 101
    assert eh[0]["last_kill_at"] == 1716000000
    vp = data["killed_encounters"]["Veeshan's Peak"]
    assert vp[0]["encounter_name"] == "Druushk"
    assert vp[0]["kill_count"] == 2


@pytest.mark.asyncio
async def test_get_progress_cached_guild_short_circuits_parses_fallback(app):
    """A warm character_cache hit skips the parses-DB fallback entirely."""

    class _Cached:
        guild_name = "Exordium"

    with (
        patch(
            "backend.server.core.primary_guild.get_active_claims",
            new_callable=AsyncMock,
            return_value={
                "approved": [{"character_name": "Sihtric", "is_primary": 1}],
                "pending": None,
            },
        ),
        patch("backend.server.core.primary_guild.character_cache.get_stale", return_value=(_Cached(), True)),
        patch("backend.server.api.zones._most_recent_parsed_guild_sync") as m_fallback,
        patch("backend.server.api.zones._compute_progress_sync", return_value={}),
    ):
        async with _signed_in_client(app) as client:
            r = await client.get("/api/zones/progress")

    assert r.status_code == 200
    assert r.json()["guild_name"] == "Exordium"
    m_fallback.assert_not_called()
