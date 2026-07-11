from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.users_db import point_users_db_at


@pytest.mark.asyncio
async def test_server_endpoint_reflects_subdomain(app, monkeypatch, tmp_path):
    from backend.server import db, server_context

    p = tmp_path / "users.db"
    db.init_db(p)
    point_users_db_at(monkeypatch, p)
    server_context.load_registry()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/server", headers={"host": "wuoshi.eq2lexicon.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["world"] == "Wuoshi"
    assert body["display_name"] == "Wuoshi"
    assert "max_level" in body and "current_xpac" in body and "launch_dt" in body
    assert any(s["subdomain"] == "varsoon" for s in body["servers"])


@pytest.mark.asyncio
async def test_server_endpoint_unknown_host_defaults(app, monkeypatch, tmp_path):
    from backend.server import db, server_context

    p = tmp_path / "users.db"
    db.init_db(p)
    point_users_db_at(monkeypatch, p)
    server_context.load_registry()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/server", headers={"host": "localhost"})
    assert r.json()["world"] == "Varsoon"
