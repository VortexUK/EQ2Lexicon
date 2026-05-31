"""Unit tests for the capability-driven auth gate (require_capability) and
the DB primitives it sits on top of."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# require_capability — factory-level guard
# ---------------------------------------------------------------------------


def test_require_capability_rejects_unknown_capability_at_definition_time():
    """Typo guard: factory raises immediately rather than at request time
    so the misconfiguration surfaces in CI / on import."""
    from backend.server.auth_deps import require_capability

    with pytest.raises(ValueError, match="Unknown capability"):
        require_capability("edit_strategy_typo")


# ---------------------------------------------------------------------------
# require_capability("edit_content") — request-time behaviour
#
# The strategy route's PUT is the canonical caller; we exercise it here to
# avoid duplicating fake-app plumbing.
# ---------------------------------------------------------------------------


def _fake_zone() -> dict:
    return {
        "name": "The Emerald Halls",
        "expansion_short": "EoF",
        "bosses": [{"encounter_name": "Prince Thirneg", "position": 1, "stage": "First Floor"}],
    }


@pytest.mark.asyncio
async def test_admin_passes_without_touching_role_tables(app):
    """Admin shortcut returns before any DB role query — proves the cheap
    path stays cheap."""
    with (
        patch("backend.server.auth_deps.require_user_session", return_value={"id": "admin-1", "username": "admin"}),
        patch("backend.server.auth_deps.is_admin", return_value=True),
        patch("backend.server.auth_deps.users_db.user_has_capability_via_db", new_callable=AsyncMock) as m_db,
        patch("backend.server.auth_deps.users_db.role_has_capability", new_callable=AsyncMock) as m_role,
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch(
            "backend.server.api.raid_strategies._write_strategy_sync",
            return_value={
                "id": 1,
                "mob_name": "Prince Thirneg",
                "position": 1,
                "strategy_md": "x",
                "source": "manual",
                "last_edited_at": 1,
                "last_edited_by": "admin-1",
            },
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/strategy",
                json={"markdown": "x"},
            )
    assert r.status_code == 200
    m_db.assert_not_called()
    m_role.assert_not_called()


@pytest.mark.asyncio
async def test_db_role_with_capability_passes(app):
    """JOIN'd capability hit short-circuits before the officer branch."""
    with (
        patch("backend.server.auth_deps.require_user_session", return_value={"id": "contrib-7", "username": "contrib"}),
        patch("backend.server.auth_deps.is_admin", return_value=False),
        patch(
            "backend.server.auth_deps.users_db.user_has_capability_via_db", new_callable=AsyncMock, return_value=True
        ),
        # role_has_capability shouldn't be reached.
        patch("backend.server.auth_deps.users_db.role_has_capability", new_callable=AsyncMock) as m_role,
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch(
            "backend.server.api.raid_strategies._write_strategy_sync",
            return_value={
                "id": 1,
                "mob_name": "Prince Thirneg",
                "position": 1,
                "strategy_md": "x",
                "source": "manual",
                "last_edited_at": 1,
                "last_edited_by": "contrib-7",
            },
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/strategy",
                json={"markdown": "x"},
            )
    assert r.status_code == 200
    m_role.assert_not_called()


@pytest.mark.asyncio
async def test_officer_check_skipped_when_role_lacks_capability(app):
    """If the role_permissions table says officers can't do this capability,
    the dep doesn't even run the dynamic check — saves a Census fallback for
    capabilities officers wouldn't qualify for anyway."""
    with (
        patch("backend.server.auth_deps.require_user_session", return_value={"id": "rando-9", "username": "rando"}),
        patch("backend.server.auth_deps.is_admin", return_value=False),
        patch(
            "backend.server.auth_deps.users_db.user_has_capability_via_db", new_callable=AsyncMock, return_value=False
        ),
        patch("backend.server.auth_deps.users_db.role_has_capability", new_callable=AsyncMock, return_value=False),
        # Officer-resolution helpers shouldn't be touched.
        patch("backend.server.api.raid_strategies._primary_guild_from_cache") as m_guild,
        patch("backend.server.api.guild._officer_chars") as m_officer,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/strategy",
                json={"markdown": "x"},
            )
    assert r.status_code == 403
    m_guild.assert_not_called()
    m_officer.assert_not_called()


@pytest.mark.asyncio
async def test_officer_path_grants_via_table_lookup(app):
    """Non-admin non-DB-role user passes the dynamic officer branch when
    role_permissions has the (officer, capability) row."""

    class _Cached:
        guild_name = "Exordium"

    with (
        patch("backend.server.auth_deps.require_user_session", return_value={"id": "officer-9", "username": "officer"}),
        patch("backend.server.auth_deps.is_admin", return_value=False),
        patch(
            "backend.server.auth_deps.users_db.user_has_capability_via_db", new_callable=AsyncMock, return_value=False
        ),
        patch("backend.server.auth_deps.users_db.role_has_capability", new_callable=AsyncMock, return_value=True),
        patch(
            "backend.server.core.primary_guild.get_active_claims",
            new_callable=AsyncMock,
            return_value={"approved": [{"character_name": "Sigarth", "is_primary": 1}], "pending": None},
        ),
        patch("backend.server.core.primary_guild.character_cache.get_stale", return_value=(_Cached(), True)),
        patch("backend.server.api.guild._officer_chars", return_value={"sigarth"}),
        patch("backend.server.api.raid_strategies.zones_db.find_by_name", return_value=_fake_zone()),
        patch(
            "backend.server.api.raid_strategies._write_strategy_sync",
            return_value={
                "id": 1,
                "mob_name": "Prince Thirneg",
                "position": 1,
                "strategy_md": "x",
                "source": "manual",
                "last_edited_at": 1,
                "last_edited_by": "officer-9",
            },
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/strategy",
                json={"markdown": "x"},
            )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_none_of_the_three_branches_grant_returns_403(app):
    with (
        patch("backend.server.auth_deps.require_user_session", return_value={"id": "rando-9", "username": "rando"}),
        patch("backend.server.auth_deps.is_admin", return_value=False),
        patch(
            "backend.server.auth_deps.users_db.user_has_capability_via_db", new_callable=AsyncMock, return_value=False
        ),
        patch("backend.server.auth_deps.users_db.role_has_capability", new_callable=AsyncMock, return_value=True),
        patch(
            "backend.server.core.primary_guild.get_active_claims",
            new_callable=AsyncMock,
            return_value={"approved": [], "pending": None},
        ),
        patch("backend.server.core.primary_guild.character_cache.get_stale", return_value=(None, False)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.put(
                "/api/zones/The Emerald Halls/encounters/1/strategy",
                json={"markdown": "x"},
            )
    assert r.status_code == 403
    assert "edit_content" in r.json()["detail"]
