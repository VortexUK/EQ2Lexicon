"""Tests for /api/supporters — the public supporter-IDs list endpoint
that drives the 👑 badge in the frontend."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.supporters_cache import _supporters_cache_isolation  # noqa: F401


@pytest.mark.asyncio
async def test_supporters_empty_when_no_one_has_role(app):
    """No supporter rows → returns an empty list, not a 500. Edge case for
    a fresh deploy with no donations yet."""
    with patch(
        "backend.server.api.supporters.users_db.list_role_assignments",
        new=AsyncMock(return_value={}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/supporters")

    assert r.status_code == 200
    assert r.json() == {"supporter_ids": []}


@pytest.mark.asyncio
async def test_supporters_returns_only_users_with_supporter_role(app):
    """Mixed role assignments → only users whose roles include 'supporter'
    are returned. Sorted output so the frontend can render in a stable
    order without sorting itself."""
    assignments = {
        "100": ["contributor", "supporter"],  # contributor AND supporter
        "200": ["supporter"],  # supporter only
        "300": ["contributor"],  # contributor only — excluded
        "400": ["supporter"],  # supporter only
        "500": [],  # empty roles — excluded
    }

    with patch(
        "backend.server.api.supporters.users_db.list_role_assignments",
        new=AsyncMock(return_value=assignments),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/supporters")

    assert r.status_code == 200
    assert r.json() == {"supporter_ids": ["100", "200", "400"]}


@pytest.mark.asyncio
async def test_supporters_response_is_cached(app):
    """Two back-to-back requests result in ONE DB call. The cache is
    module-level so subsequent requests in the same process hit memory."""
    mock = AsyncMock(return_value={"42": ["supporter"]})
    with patch("backend.server.api.supporters.users_db.list_role_assignments", mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/api/supporters")
            r2 = await client.get("/api/supporters")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json() == {"supporter_ids": ["42"]}
    # Only ONE DB query despite TWO HTTP requests.
    assert mock.await_count == 1


@pytest.mark.asyncio
async def test_supporters_invalidate_forces_refetch(app):
    """After invalidate() is called (admin grant/revoke), the next
    request hits the DB again. This is the contract the admin route
    relies on to show a freshly-granted badge immediately."""
    from backend.server.api import supporters as supporters_mod

    side_effect = [
        {"42": ["supporter"]},  # first load
        {"42": ["supporter"], "99": ["supporter"]},  # after invalidate
    ]
    mock = AsyncMock(side_effect=side_effect)
    with patch("backend.server.api.supporters.users_db.list_role_assignments", mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/api/supporters")
            assert r1.json() == {"supporter_ids": ["42"]}

            supporters_mod.invalidate()

            r2 = await client.get("/api/supporters")
            assert r2.json() == {"supporter_ids": ["42", "99"]}

    assert mock.await_count == 2  # cache busted between the two requests


@pytest.mark.asyncio
async def test_supporters_endpoint_is_public(app):
    """No auth required — the supporter list is intentionally public
    (it powers a cosmetic badge anyone can see) and the data is just
    opaque Discord IDs already visible in any guild the user is in."""
    with patch(
        "backend.server.api.supporters.users_db.list_role_assignments",
        new=AsyncMock(return_value={}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # No Authorization header, no session cookie.
            r = await client.get("/api/supporters")
    assert r.status_code == 200
