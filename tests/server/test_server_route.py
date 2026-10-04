from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def users_db(users_schema: str) -> str:
    """Isolated leased schema per test (conftest ``users_schema``) with the
    registry loaded from its seeded servers rows (Varsoon default + Wuoshi)."""
    from backend.server import server_context

    server_context.load_registry()
    return users_schema


@pytest.mark.asyncio
async def test_server_endpoint_reflects_subdomain(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/server", headers={"host": "wuoshi.eq2lexicon.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["world"] == "Wuoshi"
    assert body["display_name"] == "Wuoshi"
    assert "max_level" in body and "current_xpac" in body and "launch_dt" in body
    assert any(s["subdomain"] == "varsoon" for s in body["servers"])


@pytest.mark.asyncio
async def test_server_endpoint_unknown_host_defaults(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/server", headers={"host": "localhost"})
    assert r.json()["world"] == "Varsoon"
