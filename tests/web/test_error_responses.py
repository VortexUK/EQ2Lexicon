"""Request_id surfaces in 4xx/5xx JSON responses (Phase 3.3)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from web.app import create_app


@pytest.fixture
def app():
    return create_app()


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
