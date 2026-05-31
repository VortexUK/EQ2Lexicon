"""Confirm that officer-only users (non-admin, non-contributor) cannot write
raid strategies or zone overviews after the edit_content grant was removed.

The key test condition: ``role_has_capability('officer', 'edit_content')``
returns False (because the row no longer exists in role_permissions after the
2026-05-29 change). The dep short-circuits: the dynamic officer check is
never reached and a 403 is returned."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


def _fake_zone() -> dict:
    return {
        "name": "The Emerald Halls",
        "expansion_short": "EoF",
        "bosses": [{"encounter_name": "Prince Thirneg", "position": 1, "stage": "First Floor"}],
    }


@pytest.mark.asyncio
async def test_officer_cannot_put_encounter_strategy(app):
    """Officer-only → 403 on PUT encounter strategy; dynamic check skipped."""
    with (
        patch("backend.server.auth_deps.require_user_session", return_value={"id": "officer-X", "username": "officer"}),
        patch("backend.server.auth_deps.is_admin", return_value=False),
        patch(
            "backend.server.auth_deps.users_db.user_has_capability_via_db", new_callable=AsyncMock, return_value=False
        ),
        # The key condition: officer row is gone from role_permissions.
        patch("backend.server.auth_deps.users_db.role_has_capability", new_callable=AsyncMock, return_value=False),
        patch("backend.server.api.raid_strategies._primary_guild_from_cache") as mock_g,
        patch("backend.server.api.guild._officer_chars") as mock_o,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/strategy",
                json={"markdown": "officer sneaking in"},
            )

    assert r.status_code == 403
    assert "edit_content" in r.json()["detail"]
    # Dynamic officer machinery should NOT have run.
    mock_g.assert_not_called()
    mock_o.assert_not_called()


@pytest.mark.asyncio
async def test_officer_cannot_put_zone_overview(app):
    """Officer-only → 403 on PUT zone overview."""
    with (
        patch("backend.server.auth_deps.require_user_session", return_value={"id": "officer-X", "username": "officer"}),
        patch("backend.server.auth_deps.is_admin", return_value=False),
        patch(
            "backend.server.auth_deps.users_db.user_has_capability_via_db", new_callable=AsyncMock, return_value=False
        ),
        # The key condition: officer row is gone from role_permissions.
        patch("backend.server.auth_deps.users_db.role_has_capability", new_callable=AsyncMock, return_value=False),
        patch("backend.server.api.raid_strategies._primary_guild_from_cache") as mock_g,
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/overview",
                json={"markdown": "officer sneaking in"},
            )

    assert r.status_code == 403
    assert "edit_content" in r.json()["detail"]
    mock_g.assert_not_called()


@pytest.mark.asyncio
async def test_contributor_still_can_put_strategy(app):
    """Sanity-check: contributor (DB-role with edit_content) still passes after
    the officer grant removal."""
    fresh_row = {
        "id": 1,
        "mob_name": "Prince Thirneg",
        "position": 1,
        "strategy_md": "contrib content",
        "source": "manual",
        "last_edited_at": 1716300000,
        "last_edited_by": "contrib-1",
    }
    with (
        patch("backend.server.auth_deps.require_user_session", return_value={"id": "contrib-1", "username": "contrib"}),
        patch("backend.server.auth_deps.is_admin", return_value=False),
        patch(
            "backend.server.auth_deps.users_db.user_has_capability_via_db", new_callable=AsyncMock, return_value=True
        ),
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("backend.server.api.raid_strategies._write_strategy_sync", return_value=fresh_row),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/strategy",
                json={"markdown": "contrib content"},
            )

    assert r.status_code == 200


@pytest.mark.asyncio
async def test_contributor_still_can_put_overview(app):
    """Sanity-check: contributor still passes for the zone overview PUT."""
    fresh_row = {
        "zone_name": "The Emerald Halls",
        "overview_md": "contrib overview",
        "source": "manual",
        "last_edited_at": 1716300000,
        "last_edited_by": "contrib-1",
    }
    with (
        patch("backend.server.auth_deps.require_user_session", return_value={"id": "contrib-1", "username": "contrib"}),
        patch("backend.server.auth_deps.is_admin", return_value=False),
        patch(
            "backend.server.auth_deps.users_db.user_has_capability_via_db", new_callable=AsyncMock, return_value=True
        ),
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch("backend.server.api.raid_strategies._write_overview_sync", return_value=fresh_row),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/overview",
                json={"markdown": "contrib overview"},
            )

    assert r.status_code == 200
