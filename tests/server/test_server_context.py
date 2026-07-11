from __future__ import annotations

from backend.server import server_context as sc


def _seed(monkeypatch, tmp_path):
    from backend.server import db

    p = tmp_path / "users.db"
    db.init_db(p)
    point_users_db_at(monkeypatch, p)
    sc.load_registry()
    return p


def test_resolve_known_subdomain(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    assert sc.resolve_host("wuoshi.eq2lexicon.com").world == "Wuoshi"
    assert sc.resolve_host("varsoon.eq2lexicon.com").world == "Varsoon"


def test_resolve_unknown_falls_back_to_default(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    assert sc.resolve_host("localhost:8000").world == "Varsoon"
    assert sc.resolve_host("eq2lexicon.com").world == "Varsoon"
    assert sc.resolve_host("").world == "Varsoon"


def test_current_world_default_outside_request(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    assert sc.current_world() == "Varsoon"


def test_contextvar_roundtrip(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    wuoshi = sc.resolve_host("wuoshi.eq2lexicon.com")
    token = sc.set_active_server(wuoshi)
    try:
        assert sc.current_world() == "Wuoshi"
        assert sc.current_server().display_name == "Wuoshi"
    finally:
        sc.reset_active_server(token)
    assert sc.current_world() == "Varsoon"


def test_override_ignored_when_disabled(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    # Simulate production: the X-Server/?server= override must be ignored.
    monkeypatch.setattr(sc, "_ALLOW_OVERRIDE", False)
    assert sc.resolve_host("varsoon.eq2lexicon.com", override="wuoshi").world == "Varsoon"
    # And when allowed (dev), the override wins.
    monkeypatch.setattr(sc, "_ALLOW_OVERRIDE", True)
    assert sc.resolve_host("varsoon.eq2lexicon.com", override="wuoshi").world == "Wuoshi"


def test_default_server_returns_is_default_server(monkeypatch, tmp_path):
    """default_server() should return whichever server has is_default=True, even if
    it doesn't match EQ2_WORLD."""
    from backend.server import db

    p = tmp_path / "users.db"
    db.init_db(p)
    point_users_db_at(monkeypatch, p)
    # Set Wuoshi as the default in the DB.
    db.set_default_server_sync("Wuoshi")

    sc.load_registry()

    assert sc.default_server().world == "Wuoshi"


import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.users_db import point_users_db_at


@pytest.mark.asyncio
async def test_middleware_sets_world_from_host(monkeypatch, tmp_path):
    from fastapi import FastAPI

    from backend.server import server_context as sc2

    _seed(monkeypatch, tmp_path)
    app = FastAPI()
    app.add_middleware(sc2.ServerContextMiddleware)

    @app.get("/w")
    async def _w():
        return {"world": sc2.current_world()}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://wuoshi.eq2lexicon.com") as c:
        r = await c.get("/w", headers={"host": "wuoshi.eq2lexicon.com"})
    assert r.json()["world"] == "Wuoshi"
    async with AsyncClient(transport=transport, base_url="http://x") as c:
        r = await c.get("/w", headers={"host": "varsoon.eq2lexicon.com"})
    assert r.json()["world"] == "Varsoon"
