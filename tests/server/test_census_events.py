from __future__ import annotations

import asyncio

import pytest

from backend.server import census_events as ev


@pytest.mark.asyncio
async def test_subscriber_receives_published_event():
    ev._reset_for_test()
    q = ev.subscribe()
    try:
        ev.publish({"type": "character", "key": "menludiir:varsoon", "data": {"level": 90}})
        got = await asyncio.wait_for(q.get(), timeout=1)
        assert got["type"] == "character"
        assert got["data"]["level"] == 90
    finally:
        ev.unsubscribe(q)


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    ev._reset_for_test()
    q = ev.subscribe()
    ev.unsubscribe(q)
    ev.publish({"type": "health", "status": "down"})
    assert q.empty()
