from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_classes_endpoint_returns_all_26(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/classes")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 26
    by_name = {c["name"]: c for c in data}
    templar = by_name["Templar"]
    assert templar["archetype"] == "Priest"
    assert templar["subclass"] == "Cleric"
    assert templar["role"] == "Healer"
    assert templar["icon_url"] == "/class-icons/13.png"
    assert by_name["Channeler"]["subclass"] is None
    assert [c["display_order"] for c in data] == list(range(26))
