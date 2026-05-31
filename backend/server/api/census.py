"""Census availability endpoints: a JSON health snapshot for first paint, and an
SSE stream that pushes refresh records + health changes to the browser."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from backend.server import census_events, census_health

router = APIRouter(tags=["census"])


@router.get("/census/health")
async def get_census_health() -> dict:
    return census_health.get_state()


@router.get("/census/stream")
async def census_stream(request: Request) -> StreamingResponse:
    async def gen():
        q = census_events.subscribe()
        # Prime the client with the current health snapshot.
        yield _sse({"type": "health", **census_health.get_state()})
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=20)
                    yield _sse(event)
                except TimeoutError:
                    yield ": keep-alive\n\n"  # comment ping survives proxy idle timeouts
        finally:
            census_events.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"
