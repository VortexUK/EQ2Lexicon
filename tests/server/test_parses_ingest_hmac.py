"""HMAC validation survives a body-reading middleware between SessionMiddleware and the
ingest route: if a middleware consumes receive() without preserving Starlette's body
cache, the HMAC hashes different bytes from the parsed model and every upload 401s.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware

from tests.server._parses_ingest_fixtures import (
    _fake_require_user,
    _minimal_payload,
    _sign,
)


class _NoOpBodyReadingMiddleware(BaseHTTPMiddleware):
    """Reads body, then forwards unchanged — proves the cache survives."""

    async def dispatch(self, request: Request, call_next):
        _ = await request.body()  # The exact pattern a debugging middleware would use.
        return await call_next(request)


@pytest.mark.asyncio
async def test_hmac_validation_survives_body_reading_middleware(app) -> None:
    """Inject a no-op body-reading middleware and verify the happy-path
    ingest still returns 201. If Starlette's body cache is broken by the
    middleware chain, the HMAC will mismatch and ingest will 401 instead.

    This test pins the assumption documented in parses.py's
    _validate_payload_signature comment — see the ASSUMPTION block there.
    """
    # Inject the middleware AFTER the app is created. Starlette adds
    # middleware in reverse order (last-added = outermost), so this
    # middleware will run before the route but after SessionMiddleware.
    app.add_middleware(_NoOpBodyReadingMiddleware)

    token = "eq2c_middleware_test"
    payload = _minimal_payload()
    body_bytes = json.dumps(payload).encode("utf-8")
    signature = _sign(body_bytes, token)

    sync_result = ("inserted", 42, 2, 0, 0)
    with (
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("backend.server.api.parses.ingest._resolve_combatant_snapshots", new=AsyncMock(return_value={})),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/parses/ingest",
                content=body_bytes,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Lexicon-Signature": signature,
                    "Content-Type": "application/json",
                },
            )

    assert res.status_code == 201, res.text
