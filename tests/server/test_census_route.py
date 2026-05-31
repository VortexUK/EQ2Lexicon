from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.server import census_health as ch


@pytest.mark.asyncio
async def test_health_endpoint(app):
    ch._reset_for_test()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/census/health")
    assert r.status_code == 200
    assert r.json()["status"] in ("up", "down", "unknown")
