"""Tests for the guild route — caching behaviour and endpoint responses."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.census import store as census_store
from backend.census.models import GuildData, GuildMember
from backend.server.api.guild import GuildInfoResponse, GuildMemberResponse, GuildResponse


def _make_member(
    name: str = "Sihtric", cls: str = "Shadowknight", rank: str = "Officer", rank_id: int = 1
) -> GuildMemberResponse:
    return GuildMemberResponse(name=name, level=100, cls=cls, rank=rank, rank_id=rank_id)


def _make_guild_response(name: str = "Exordium") -> GuildResponse:
    return GuildResponse(
        name=name,
        world="Varsoon",
        members=[
            _make_member("Sihtric", rank="Officer", rank_id=1),
            _make_member("Menludiir", cls="Wizard", rank="Member", rank_id=2),
        ],
    )


def _make_guild_info(name: str = "Exordium") -> GuildInfoResponse:
    return GuildInfoResponse(name=name, world="Varsoon", level=300, members=42)


def _make_guild_member_model(name: str = "Sihtric") -> GuildMember:
    return GuildMember(
        name=name,
        level=100,
        cls="Shadowknight",
        ts_class=None,
        ts_level=None,
        aa_level=320,
        deity=None,
        rank="Officer",
        rank_id=1,
    )


# ---------------------------------------------------------------------------
# Roster endpoint  (GET /api/guild/{guild_name})
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guild_roster_cache_hit(app):
    """Returns cached roster immediately without calling Census."""
    cached_roster = _make_guild_response()

    with patch("backend.server.api.guild.guild_cache") as mock_cache:
        mock_cache.get_stale.return_value = (cached_roster, False)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/guild/Exordium")

    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Exordium"
    assert len(data["members"]) == 2
    names = {m["name"] for m in data["members"]}
    assert "Sihtric" in names


@pytest.mark.asyncio
async def test_guild_roster_not_found(app):
    """404 when Census returns nothing for an unknown guild."""
    with (
        patch("backend.server.api.guild.guild_cache") as mock_cache,
        patch("backend.server.api.guild.census_store") as mock_cs,
        patch("backend.server.census_health.is_down", return_value=False),
        patch("backend.server.api.guild._persist_and_publish_guild", new_callable=AsyncMock) as mock_persist,
    ):
        mock_cache.get_stale.return_value = (None, False)
        mock_cs.DB_PATH = census_store.DB_PATH
        mock_cs.init_db.return_value = MagicMock()
        mock_cs.get_guild.return_value = None  # not in store
        mock_persist.return_value = None
        # After persist, cache is still empty → 404
        mock_cache.get_stale.return_value = (None, False)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/guild/NoSuchGuild")

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_guild_roster_stale_triggers_background_refresh(app):
    """Stale in-memory hit falls through to the store; a stale stored entry
    queues a background refresh and returns the stored data (stale=True)."""
    cached_roster = _make_guild_response()
    # Store blob uses the canonical {"roster": ..., "info": ...} shape written by _persist_and_publish_guild
    stored_blob = {"roster": cached_roster.model_dump(), "info": None}

    refresh_calls: list[str] = []

    with (
        patch("backend.server.api.guild.guild_cache") as mock_cache,
        patch("backend.server.api.guild.census_store") as mock_cs,
        patch("backend.server.census_health.is_down", return_value=False),
        patch("backend.server.census_refresh.request_guild_refresh", side_effect=lambda n: refresh_calls.append(n)),
    ):
        mock_cache.get_stale.return_value = (cached_roster, True)  # stale in-memory
        mock_cs.DB_PATH = census_store.DB_PATH
        mock_cs.init_db.return_value = MagicMock()
        # Store has data but it's old (last_resolved_at=1 → very stale)
        mock_cs.get_guild.return_value = {"data": stored_blob, "last_resolved_at": 1}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/guild/Exordium")

    assert r.status_code == 200
    body = r.json()
    assert body["stale"] is True
    # A background refresh should have been requested
    assert len(refresh_calls) == 1, "Expected exactly one background refresh request"


# ---------------------------------------------------------------------------
# Info endpoint  (GET /api/guild/{guild_name}/info)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guild_info_cache_hit(app):
    """Returns cached guild info without calling Census."""
    cached_info = _make_guild_info()

    with patch("backend.server.api.guild.guild_cache") as mock_cache:
        mock_cache.get_stale.return_value = (cached_info, False)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/guild/Exordium/info")

    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Exordium"
    assert data["world"] == "Varsoon"
    assert data["level"] == 300


@pytest.mark.asyncio
async def test_guild_info_not_found(app):
    """404 when Census returns nothing."""
    with (
        patch("backend.server.api.guild.guild_cache") as mock_cache,
        patch("backend.server.api.guild.census_store") as mock_cs,
        patch("backend.server.census_health.is_down", return_value=False),
        patch("backend.server.api.guild._persist_and_publish_guild", new_callable=AsyncMock) as mock_persist,
    ):
        mock_cache.get_stale.return_value = (None, False)
        mock_cs.DB_PATH = census_store.DB_PATH
        mock_cs.init_db.return_value = MagicMock()
        mock_cs.get_guild.return_value = None
        mock_persist.return_value = None

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/guild/NoSuchGuild/info")

    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Cache pre-warming: single fetch sets both roster and info keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_and_cache_guild_populates_roster_and_info(app):
    """After a cache miss, _fetch_and_cache_guild sets both roster and info keys."""
    guild_data = GuildData(
        name="Exordium",
        world="Varsoon",
        members=[_make_guild_member_model()],
    )
    overviews = []
    guild_info = {
        "name": "Exordium",
        "world": "Varsoon",
        "level": 300,
        "members": 42,
        "accounts": 30,
        "achievement_count": 5,
        "dateformed": None,
        "description": None,
        "alignment": None,
        "type": None,
    }

    roster_stubs = [{"name": "Sihtric", "rank": "Officer", "rank_id": 1}]

    mock_client = AsyncMock()
    mock_client.get_guild_full = AsyncMock(return_value=(guild_data, overviews, guild_info, roster_stubs))
    mock_client.close = AsyncMock()

    set_calls: list[tuple] = []

    def _record_set(key, value):
        set_calls.append((key, value))

    roster_response = GuildResponse(
        name="Exordium",
        world="Varsoon",
        members=[GuildMemberResponse(name="Sihtric", level=100, cls="Shadowknight")],
    )

    call_count = 0

    def _get_stale_side_effect(key):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (None, False)  # first call → cache miss, triggers fetch
        if "roster_stubs:" in key:
            return ([{"name": "Sihtric", "rank": "Officer", "rank_id": 1}], False)
        if "roster:" in key:
            return (roster_response, False)
        return (None, False)

    with (
        patch("backend.server.guild_cache.guild_cache") as mock_cache,
        patch("backend.server.guild_cache.character_cache"),
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient", return_value=mock_client),
        patch("backend.server.guild_cache.census_store") as mock_cs,
        patch("backend.server.census_health.is_down", return_value=False),
    ):
        mock_cache.get_stale.side_effect = _get_stale_side_effect
        mock_cache.set.side_effect = _record_set
        # Store is empty for this test — exercise the live-fetch path
        mock_cs.DB_PATH = census_store.DB_PATH
        mock_cs.init_db.return_value = MagicMock()
        mock_cs.get_guild.return_value = None

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/api/guild/Exordium")

    # Both roster and info cache keys should have been written
    written_keys = {k for k, _ in set_calls}
    assert any("roster:" in k for k in written_keys), f"Expected roster key in {written_keys}"
    assert any("info:" in k for k in written_keys), f"Expected info key in {written_keys}"


# ---------------------------------------------------------------------------
# Store-served roster: Census-down + stored guild → 200 with stale=True
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_census_db(tmp_path, monkeypatch):
    """Seed a temporary census.db with one guild row and redirect DB_PATH to it."""
    db_path = tmp_path / "backend.census.db"
    monkeypatch.setattr(census_store, "DB_PATH", db_path)
    monkeypatch.setenv("CENSUS_DB_PATH", str(db_path))
    roster_blob = GuildResponse(
        name="Exordium",
        world="Varsoon",
        members=[
            GuildMemberResponse(
                name="Sihtric",
                level=100,
                cls="Shadowknight",
                rank="Officer",
                rank_id=1,
            )
        ],
        fetched_at=1000,
        stale=False,
    ).model_dump()
    info_blob = GuildInfoResponse(
        name="Exordium",
        world="Varsoon",
        level=300,
        members=1,
        accounts=1,
        achievement_count=5,
        dateformed=1234567890,
        description="A test guild",
        alignment=0,
        type=0,
    ).model_dump()
    combined_blob = {"roster": roster_blob, "info": info_blob}
    conn = census_store.init_db(db_path)
    census_store.upsert_guild(conn, "Exordium", "Varsoon", combined_blob, now=1000)
    conn.close()
    return db_path


@pytest.mark.asyncio
async def test_guild_roster_served_from_store_when_census_down(app, tmp_census_db, monkeypatch):
    """Census-down + a stored guild → 200 with stale=True, member present."""
    import backend.server.census_health as _health_mod

    monkeypatch.setattr(_health_mod, "is_down", lambda: True)
    # Redirect the module-level DB_PATH so the route's local init_db call uses the tmp db.
    monkeypatch.setattr(census_store, "DB_PATH", tmp_census_db)

    from backend.server.cache import guild_cache

    guild_cache.delete("roster:exordium:varsoon")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/guild/Exordium")

    assert r.status_code == 200
    body = r.json()
    assert len(body["members"]) == 1
    assert body["members"][0]["name"] == "Sihtric"
    assert body["stale"] is True


@pytest.mark.asyncio
async def test_guild_info_served_from_store_when_census_down(app, tmp_census_db, monkeypatch):
    """Census-down + a stored guild → info endpoint returns full info fields, stale=True."""
    import backend.server.census_health as _health_mod

    monkeypatch.setattr(_health_mod, "is_down", lambda: True)
    monkeypatch.setattr(census_store, "DB_PATH", tmp_census_db)

    from backend.server.cache import guild_cache

    guild_cache.delete("info:exordium:varsoon")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/guild/Exordium/info")

    assert r.status_code == 200
    body = r.json()
    assert body["stale"] is True
    # Non-degraded fields from the stored info blob must survive Census-down
    assert body["level"] == 300
    assert body["dateformed"] == 1234567890


# ---------------------------------------------------------------------------
# Best-known merged roster: offline members carried forward from the store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_merges_offline_member_from_store(app, tmp_path, monkeypatch):
    """_persist_and_publish_guild merges fresh-resolved members with last-good
    data for offline members (carried from the store), omits never-seen members,
    and does NOT bump the carried-forward member's last_resolved_at."""
    from backend.census.config import WORLD as _WORLD
    from backend.census.models import CharacterOverview, GuildData, GuildMember
    from backend.server.api.guild import _persist_and_publish_guild
    from backend.server.cache import guild_cache

    db_path = tmp_path / "backend.census.db"
    monkeypatch.setattr(census_store, "DB_PATH", db_path)
    monkeypatch.setenv("CENSUS_DB_PATH", str(db_path))

    # Seed an offline alt that resolved in the PAST (now=1000).
    conn = census_store.init_db(db_path)
    census_store.upsert_character(
        conn,
        "OfflineAlt",
        _WORLD,
        {"name": "OfflineAlt", "level": 80, "cls": "Fury"},
        resolved=True,
        now=1000,
    )
    conn.close()

    # Census returns ONLY OnlineMain resolved, but the member LIST (stubs) carries
    # OnlineMain + OfflineAlt + GhostNoData (never-seen, no data anywhere).
    guild_data = GuildData(
        name="TestGuild",
        world=_WORLD,
        members=[
            GuildMember(
                name="OnlineMain",
                level=90,
                cls="Templar",
                ts_class=None,
                ts_level=None,
                aa_level=300,
                deity=None,
                rank="Leader",
                rank_id=0,
            )
        ],
    )
    overviews = [
        CharacterOverview(
            id="1",
            name="OnlineMain",
            level=90,
            cls="Templar",
            race="Human",
            gender="Male",
            deity=None,
            aa_count=300,
            world=_WORLD,
        )
    ]
    guild_info = {
        "name": "TestGuild",
        "world": _WORLD,
        "level": 300,
        "members": 3,
        "accounts": 3,
        "achievement_count": 0,
        "dateformed": None,
        "description": None,
        "alignment": None,
        "type": None,
    }
    roster_stubs = [
        {"name": "OnlineMain", "rank": "Leader", "rank_id": 0},
        {"name": "OfflineAlt", "rank": "Member", "rank_id": 2},
        {"name": "GhostNoData", "rank": "Member", "rank_id": 2},
    ]

    mock_client = AsyncMock()
    mock_client.get_guild_full = AsyncMock(return_value=(guild_data, overviews, guild_info, roster_stubs))
    mock_client.close = AsyncMock()

    # Start from a cold cache so _fetch_and_cache_guild actually fetches.
    glower, wlower = "testguild", _WORLD.lower()
    for prefix in ("roster", "info", "roster_stubs", "adorns", "spells"):
        guild_cache.delete(f"{prefix}:{glower}:{wlower}")

    with (
        patch("backend.server.core.census_lifecycle._clients", {}),
        patch("backend.server.core.census_lifecycle.CensusClient", return_value=mock_client),
    ):
        await _persist_and_publish_guild("TestGuild")

    conn = census_store.init_db(db_path)
    try:
        guild_rec = census_store.get_guild(conn, "TestGuild", _WORLD)
        assert guild_rec is not None
        members = {m["name"]: m for m in guild_rec["data"]["roster"]["members"]}

        # Fresh member present with its fresh data.
        assert "OnlineMain" in members
        assert members["OnlineMain"]["level"] == 90

        # Offline member carried forward from the store with its last-good data.
        assert "OfflineAlt" in members
        assert members["OfflineAlt"]["level"] == 80
        assert members["OfflineAlt"]["cls"] == "Fury"
        # Rank comes from the (reliable) member list stub.
        assert members["OfflineAlt"]["rank"] == "Member"

        # Never-seen member with no data anywhere is omitted (no blank row).
        assert "GhostNoData" not in members

        # OfflineAlt was carried-forward, NOT freshly resolved → timestamp unchanged.
        alt_rec = census_store.get_character(conn, "OfflineAlt", _WORLD)
        assert alt_rec is not None
        assert alt_rec["last_resolved_at"] == 1000

        # OnlineMain genuinely resolved this fetch → record now exists, freshly stamped.
        main_rec = census_store.get_character(conn, "OnlineMain", _WORLD)
        assert main_rec is not None
        assert main_rec["last_resolved_at"] > 1000
    finally:
        conn.close()
