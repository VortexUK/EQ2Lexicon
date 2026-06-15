"""Request_id surfaces in 4xx/5xx JSON responses (Phase 3.3)."""

from __future__ import annotations

import logging

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_422_logs_failing_field_and_returns_detail(app, caplog) -> None:
    """A request-validation 422 must (a) still return detail + request_id in the
    body, and (b) log the failing field(s) server-side so 422s are diagnosable
    from logs (the plugin only sees the body). Body validation runs before the
    in-handler auth check, so an empty body 422s without credentials."""
    with caplog.at_level(logging.WARNING, logger="backend.server.app"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post("/api/parses/ingest", json={})
    assert res.status_code == 422
    body = res.json()
    assert "detail" in body and "request_id" in body
    # The new server-side log line names the path + a field locator.
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "[validation] 422" in logged
    assert "/api/parses/ingest" in logged


@pytest.mark.asyncio
async def test_404_includes_request_id(app) -> None:
    """A 404 on an unknown /api/* path returns JSON with a request_id field."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/does-not-exist-at-all")
    assert res.status_code == 404
    body = res.json()
    assert "request_id" in body
    # The header and the body field must agree.
    assert res.headers.get("x-request-id") == body["request_id"]
    # request_id must be a non-empty string (not None or "-" unless context is absent).
    assert isinstance(body["request_id"], str)
    assert body["request_id"]


@pytest.mark.asyncio
async def test_404_static_asset_returns_text_not_json(app) -> None:
    """Regression: a 404 for a non-/api/ path (typically a missing static
    asset like ``/assets/ChunkName-hash.js``) MUST NOT return
    ``Content-Type: application/json``. Browsers refuse to execute a JS
    module whose response advertises JSON, which breaks the whole SPA
    when a chunk is missing (stale CDN cache pointing at old chunk
    hashes, etc.). Plain text 404 lets the browser fail gracefully and
    the SPA's error boundary handle it."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/assets/MissingChunk-abc123.js")
    assert res.status_code == 404
    # CRITICAL: must not be application/json (that's what broke the SPA)
    ct = res.headers.get("content-type", "")
    assert "application/json" not in ct, f"got {ct!r}, must not be JSON for non-API paths"
    # request_id still surfaces in the header for log correlation.
    assert res.headers.get("x-request-id")
