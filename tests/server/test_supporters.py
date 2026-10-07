"""Tests for /api/supporters — the supporter list that drives the 👑 badge
and the Support page wall. Session-gated; returns display names, never raw Discord ids."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.fixtures.supporters_cache import _supporters_cache_isolation  # noqa: F401
from tests.fixtures.users import make_fake_require_user, make_fake_user

_fake_user = make_fake_require_user(make_fake_user(id="123456789"))


def _names(mapping: dict[str, str]):
    return patch(
        "backend.server.api.supporters.users_db.get_display_names_for_discord_ids",
        new=AsyncMock(return_value=mapping),
    )


@pytest.mark.asyncio
async def test_supporters_requires_a_session(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/supporters")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_supporters_empty_when_no_one_has_role(app):
    """No supporter rows → returns empty lists, not a 500 — and never asks
    for display names."""
    with (
        patch("backend.server.api.supporters._require_user", _fake_user),
        patch("backend.server.api.supporters.users_db.list_role_assignments", new=AsyncMock(return_value={})),
        _names({}) as names,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/supporters")
    assert r.status_code == 200
    assert r.json() == {"supporter_ids": [], "supporters": []}
    names.assert_not_awaited()


@pytest.mark.asyncio
async def test_supporters_returns_only_users_with_supporter_role_with_names(app):
    """Mixed role assignments → only users whose roles include 'supporter',
    sorted, each with the display name we know (None when the users row
    has none)."""
    assignments = {
        "100": ["contributor", "supporter"],
        "200": ["supporter"],
        "300": ["contributor"],  # excluded
        "400": ["supporter"],
        "500": [],  # excluded
    }
    with (
        patch("backend.server.api.supporters._require_user", _fake_user),
        patch(
            "backend.server.api.supporters.users_db.list_role_assignments",
            new=AsyncMock(return_value=assignments),
        ),
        _names({"100": "Alice", "400": "Dave"}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/supporters")
    assert r.status_code == 200
    assert r.json() == {
        "supporter_ids": ["100", "200", "400"],
        "supporters": [
            {"discord_id": "100", "display_name": "Alice"},
            {"discord_id": "200", "display_name": None},
            {"discord_id": "400", "display_name": "Dave"},
        ],
    }


@pytest.mark.asyncio
async def test_supporters_response_is_cached(app):
    """Two back-to-back requests result in ONE DB call."""
    mock = AsyncMock(return_value={"42": ["supporter"]})
    with (
        patch("backend.server.api.supporters._require_user", _fake_user),
        patch("backend.server.api.supporters.users_db.list_role_assignments", mock),
        _names({"42": "Zed"}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/api/supporters")
            r2 = await client.get("/api/supporters")
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert r1.json()["supporter_ids"] == ["42"]
    assert mock.await_count == 1


@pytest.mark.asyncio
async def test_supporters_invalidate_forces_refetch(app):
    """After invalidate() (admin grant/revoke, account erasure) the next
    request hits the DB again."""
    from backend.server.api import supporters as supporters_mod

    side_effect = [
        {"42": ["supporter"]},
        {"42": ["supporter"], "99": ["supporter"]},
    ]
    mock = AsyncMock(side_effect=side_effect)
    with (
        patch("backend.server.api.supporters._require_user", _fake_user),
        patch("backend.server.api.supporters.users_db.list_role_assignments", mock),
        _names({}),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/api/supporters")
            supporters_mod.invalidate()
            r2 = await client.get("/api/supporters")
    assert r1.json()["supporter_ids"] == ["42"]
    assert r2.json()["supporter_ids"] == ["42", "99"]
    assert mock.await_count == 2
