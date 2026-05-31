"""In-process async pub/sub backing the SSE stream. Each SSE connection holds a
subscriber Queue; refresh + health events fan out to all of them.

SINGLE-PROCESS ONLY: events published in one process aren't seen by another. The
app runs as one uvicorn process today; multiple workers would need a broker."""

from __future__ import annotations

import asyncio

_subscribers: set[asyncio.Queue] = set()


def _reset_for_test() -> None:
    _subscribers.clear()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(event: dict) -> None:
    """Non-blocking fan-out. A full/slow subscriber queue drops the event for
    that subscriber rather than blocking refreshers."""
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
