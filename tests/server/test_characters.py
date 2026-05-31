"""Tests for /api/characters/lookup — cache-only bulk character info."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


def _fake_cached(*, guild_name=None, cls=None, level=None):
    """Return an object that mimics a CharacterResponse for the cache."""
    return SimpleNamespace(guild_name=guild_name, cls=cls, level=level)


def _cache_with(entries: dict):
    """Build a fake character_cache whose get_stale returns the given map."""
    from unittest.mock import MagicMock

    fake = MagicMock()
    # entries keyed by cache_key (name.lower():world.lower())
    fake.get_stale.side_effect = lambda key: (entries.get(key), False)
    return fake


@pytest.mark.asyncio
async def test_lookup_empty_names_returns_empty(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/characters/lookup")
    assert r.status_code == 200
    assert r.json() == {"results": {}}


@pytest.mark.asyncio
async def test_lookup_cache_hit_returns_guild(app):
    entries = {
        "menludiir:varsoon": _fake_cached(guild_name="Exordium", cls="Templar", level=100),
    }
    with patch("backend.server.api.characters.character_cache", _cache_with(entries)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/characters/lookup?names=Menludiir")

    assert r.status_code == 200
    data = r.json()
    assert "Menludiir" in data["results"]
    entry = data["results"]["Menludiir"]
    assert entry["found"] is True
    assert entry["guild_name"] == "Exordium"
    assert entry["cls"] == "Templar"
    assert entry["level"] == 100


@pytest.mark.asyncio
async def test_lookup_cache_miss_returns_not_found(app):
    with patch("backend.server.api.characters.character_cache", _cache_with({})):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/characters/lookup?names=NotInCache")

    assert r.status_code == 200
    entry = r.json()["results"]["NotInCache"]
    assert entry["found"] is False
    assert entry["guild_name"] is None


@pytest.mark.asyncio
async def test_lookup_mixed_hits_and_misses(app):
    entries = {
        "menludiir:varsoon": _fake_cached(guild_name="Exordium"),
        "sihtric:varsoon": _fake_cached(guild_name=None),  # cached but no guild
    }
    with patch("backend.server.api.characters.character_cache", _cache_with(entries)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/characters/lookup?names=Menludiir,Sihtric,Stranger")

    data = r.json()["results"]
    assert data["Menludiir"]["found"] is True
    assert data["Menludiir"]["guild_name"] == "Exordium"
    assert data["Sihtric"]["found"] is True
    assert data["Sihtric"]["guild_name"] is None
    assert data["Stranger"]["found"] is False
    assert data["Stranger"]["guild_name"] is None


@pytest.mark.asyncio
async def test_lookup_dedupes_names(app):
    entries = {"menludiir:varsoon": _fake_cached(guild_name="Exordium")}
    with patch("backend.server.api.characters.character_cache", _cache_with(entries)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/characters/lookup?names=Menludiir,menludiir,MENLUDIIR")

    data = r.json()["results"]
    # First-seen casing wins; duplicates dropped.
    assert list(data.keys()) == ["Menludiir"]


@pytest.mark.asyncio
async def test_lookup_caps_at_50_names(app):
    with patch("backend.server.api.characters.character_cache", _cache_with({})):
        many = ",".join(f"Name{i}" for i in range(75))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(f"/api/characters/lookup?names={many}")
    data = r.json()["results"]
    assert len(data) == 50
