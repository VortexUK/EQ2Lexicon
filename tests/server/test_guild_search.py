"""Tests for /api/guilds/search — store-first with census fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


def _seed_store_guild(name: str, *, world: str = "Varsoon") -> None:
    from backend.census.store import store as census_store

    conn = census_store.init_db()
    try:
        census_store.upsert_guild(conn, name, world, {"name": name})
    finally:
        conn.close()


def _census_ctx(client: object) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.mark.asyncio
async def test_guild_search_serves_store_hits_without_census(app):
    for n in ("Zzguilda", "Zzguildb", "Zzguildc"):
        _seed_store_guild(n)
    with patch("backend.server.api.guild.shared_census_client") as census:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/guilds/search?name=Zzguild")
    assert r.status_code == 200
    assert census.call_count == 0
    assert {g["name"] for g in r.json()["results"]} == {"Zzguilda", "Zzguildb", "Zzguildc"}


@pytest.mark.asyncio
async def test_guild_search_falls_to_census_when_store_knows_too_few(app):
    _seed_store_guild("Zzgcensusa")
    fake = MagicMock()
    fake.search_guilds_by_name = AsyncMock(return_value=[{"name": "Zzgcensusb"}])
    with patch("backend.server.api.guild.shared_census_client", return_value=_census_ctx(fake)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/guilds/search?name=Zzgcensus")
    assert {g["name"] for g in r.json()["results"]} == {"Zzgcensusb"}


@pytest.mark.asyncio
async def test_guild_search_census_failure_returns_store_partials(app):
    _seed_store_guild("Zzgpartial")
    fake = MagicMock()
    fake.search_guilds_by_name = AsyncMock(side_effect=RuntimeError("census down"))
    with patch("backend.server.api.guild.shared_census_client", return_value=_census_ctx(fake)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/guilds/search?name=Zzgpartial")
    assert {g["name"] for g in r.json()["results"]} == {"Zzgpartial"}
