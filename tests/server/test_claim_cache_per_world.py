"""Tests for the per-world claim cache fix.

Before the fix, ``_claim_cache_key`` was just ``f"claims:{discord_id}"`` —
no world component. With the per-subdomain split (varsoon.eq2lexicon.com /
wuoshi.eq2lexicon.com), whichever subdomain a user hit first populated the
cache, and any subsequent request from the other subdomain got back the
wrong server's claim list (or nothing at all) for the full 5-minute TTL.

These tests exercise the route via the real ASGI app + the X-Server header
that ServerContextMiddleware honours in non-prod environments, so the cache
key flows through current_world() the same way it would in production."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server import db, server_context
from backend.server.cache import claim_cache
from backend.server.db import review_claim, submit_claim, upsert_server_settings_sync, upsert_user


@pytest.fixture(autouse=True)
def _both_servers_registered():
    """The X-Server header is resolved against the server registry. Without
    Wuoshi explicitly registered the middleware falls back to Varsoon and
    the cross-world tests collapse. Re-register both worlds + reload before
    every test; restoring the original registry isn't strictly necessary
    because conftest tears the test DB down between sessions."""
    upsert_server_settings_sync("Varsoon", max_level=50, current_xpac=None, launch_dt=None, path=db.DB_PATH)
    upsert_server_settings_sync("Wuoshi", max_level=70, current_xpac=None, launch_dt=None, path=db.DB_PATH)
    server_context.load_registry()
    yield


async def _seed_user(discord_id: str) -> None:
    await upsert_user(
        discord_id=discord_id,
        discord_name=discord_id,
        discord_username=discord_id,
        avatar=None,
        path=db.DB_PATH,
    )


class _NullCensusClient:
    """Bypass Census for unit tests — every guild lookup resolves to None
    so the response builder doesn't try to make real network calls."""

    def __init__(self, *_args, **_kwargs): ...

    async def get_character_guild_name(self, *_args, **_kwargs):
        return None

    async def close(self):
        return None


def test_cache_key_helper_is_world_scoped():
    """Unit-level pin on the key shape so any future refactor that flattens
    the key (regressing into the bug) trips a fast test."""
    from backend.server.api.claim import _claim_cache_key

    a = _claim_cache_key("user-1", "Varsoon")
    b = _claim_cache_key("user-1", "Wuoshi")
    c = _claim_cache_key("user-2", "Varsoon")
    assert a != b, "same user on different worlds must hash to different keys"
    assert a != c, "different users on same world must hash to different keys"
    assert "Varsoon" in a and "Wuoshi" in b


def _fake_user_for(discord_id: str):
    """Factory: returns a sync _require_user replacement (the real one
    in web.auth_deps is sync) that resolves to the given Discord ID.
    Different tests use different IDs so the shared test DB doesn't
    carry claim state across tests in the module."""

    def _resolved(request) -> dict:
        return {"id": discord_id, "username": discord_id}

    return _resolved


@pytest.mark.asyncio
async def test_varsoon_subdomain_returns_only_varsoon_claims(app, monkeypatch):
    """A user with one Varsoon claim, one Wuoshi claim → hitting the
    Varsoon subdomain shows ONLY the Varsoon character."""
    from backend.server.api import claim as claim_mod

    # Per-test user + character names so the shared test DB doesn't leak
    # state from sibling tests in the same module.
    uid = "csu-varsoon-only"
    await _seed_user(uid)

    v = await submit_claim(uid, "VarcharA", world="Varsoon", path=db.DB_PATH)
    await review_claim(v["id"], "approved", "admin", path=db.DB_PATH)
    w = await submit_claim(uid, "WuocharA", world="Wuoshi", path=db.DB_PATH)
    await review_claim(w["id"], "approved", "admin", path=db.DB_PATH)

    monkeypatch.setattr(claim_mod, "_require_user", _fake_user_for(uid))
    monkeypatch.setattr("backend.server.core.census_lifecycle._clients", {})
    monkeypatch.setattr("backend.server.core.census_lifecycle.CensusClient", _NullCensusClient)
    # TTLCache has no public clear() — reach into _store directly for
    # the per-test fresh start. Acceptable in tests.
    claim_cache._store.clear()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # X-Server: varsoon → ServerContextMiddleware sets current_world()="Varsoon".
        r = await client.get("/api/claim/me", headers={"X-Server": "varsoon"})

    assert r.status_code == 200, r.text
    body = r.json()
    names = [c["character_name"] for c in body["approved"]]
    assert names == ["VarcharA"], names


@pytest.mark.asyncio
async def test_wuoshi_subdomain_after_varsoon_hit_serves_own_data(app, monkeypatch):
    """The exact bug from the screenshot: user hits Varsoon first (which
    populates the cache), then visits Wuoshi. Pre-fix the Wuoshi response
    was the cached Varsoon list. Post-fix the cache keys are distinct so
    Wuoshi shows its own data."""
    from backend.server.api import claim as claim_mod

    uid = "csu-crossover"
    await _seed_user(uid)

    v = await submit_claim(uid, "VarcharB", world="Varsoon", path=db.DB_PATH)
    await review_claim(v["id"], "approved", "admin", path=db.DB_PATH)
    w = await submit_claim(uid, "WuocharB", world="Wuoshi", path=db.DB_PATH)
    await review_claim(w["id"], "approved", "admin", path=db.DB_PATH)

    monkeypatch.setattr(claim_mod, "_require_user", _fake_user_for(uid))
    monkeypatch.setattr("backend.server.core.census_lifecycle._clients", {})
    monkeypatch.setattr("backend.server.core.census_lifecycle.CensusClient", _NullCensusClient)
    # TTLCache has no public clear() — reach into _store directly for
    # the per-test fresh start. Acceptable in tests.
    claim_cache._store.clear()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Hit Varsoon first — populates the per-world cache slot.
        r_var = await client.get("/api/claim/me", headers={"X-Server": "varsoon"})
        assert [c["character_name"] for c in r_var.json()["approved"]] == ["VarcharB"]

        # Then hit Wuoshi — must NOT serve the Varsoon cache slot.
        r_wuo = await client.get("/api/claim/me", headers={"X-Server": "wuoshi"})

    assert r_wuo.status_code == 200, r_wuo.text
    names = [c["character_name"] for c in r_wuo.json()["approved"]]
    assert names == ["WuocharB"], names
