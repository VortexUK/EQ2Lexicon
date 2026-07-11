"""Tests for web.db.users role + role_request helpers — COV-012.

Uses a per-test temp DB via the project conftest _TEST_DB_DIR pattern.
Covers: grant_role, revoke_role, list_roles_for_user, has_role,
create_role_request, list_role_requests, review_and_grant_role,
withdraw_role_request, user_has_capability_via_db, role_has_capability,
set_user_access, list_all_users.

Target: ≥ 75% on web.db.users.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from backend.server.db import (
    approve_all_pending,
    create_role_request,
    get_role_request,
    get_user_access_status,
    grant_role,
    has_role,
    init_db,
    list_all_users,
    list_pending_users,
    list_role_assignments,
    list_role_requests,
    list_roles_for_user,
    review_and_grant_role,
    review_role_request,
    revoke_role,
    role_has_capability,
    set_user_access,
    upsert_user,
    user_has_capability_via_db,
    withdraw_role_request,
)
from tests.fixtures.users_db import point_users_db_at


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "users.db"
    init_db(p)
    return p


@pytest.fixture(autouse=True)
def _stores_at_db_path(db_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point users.db (constant + every domain store) at this test's temp DB."""
    point_users_db_at(monkeypatch, db_path)


async def _seed_user(db_path: Path, discord_id: str = "user-1", name: str = "TestUser") -> None:
    await upsert_user(discord_id, name, name.lower(), None)


# ---------------------------------------------------------------------------
# grant_role / revoke_role / list_roles_for_user / has_role
# ---------------------------------------------------------------------------


class TestRoleHelpers:
    @pytest.mark.asyncio
    async def test_grant_role_returns_true_on_insert(self, db_path: Path):
        await _seed_user(db_path)
        result = await grant_role("user-1", "contributor", granted_by="admin-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_grant_role_idempotent_returns_false(self, db_path: Path):
        await _seed_user(db_path)
        await grant_role("user-1", "contributor", granted_by="admin-1")
        second = await grant_role("user-1", "contributor", granted_by="admin-1")
        assert second is False

    @pytest.mark.asyncio
    async def test_revoke_role_returns_true_when_held(self, db_path: Path):
        await _seed_user(db_path)
        await grant_role("user-1", "contributor", granted_by="admin-1")
        assert await revoke_role("user-1", "contributor") is True

    @pytest.mark.asyncio
    async def test_revoke_role_returns_false_when_not_held(self, db_path: Path):
        await _seed_user(db_path)
        assert await revoke_role("user-1", "contributor") is False

    @pytest.mark.asyncio
    async def test_list_roles_for_user(self, db_path: Path):
        await _seed_user(db_path)
        await grant_role("user-1", "contributor", granted_by="admin-1")
        roles = await list_roles_for_user("user-1")
        assert "contributor" in roles

    @pytest.mark.asyncio
    async def test_has_role_returns_true_when_granted(self, db_path: Path):
        await _seed_user(db_path)
        await grant_role("user-1", "contributor", granted_by="admin-1")
        assert await has_role("user-1", "contributor") is True

    @pytest.mark.asyncio
    async def test_has_role_returns_false_when_absent(self, db_path: Path):
        await _seed_user(db_path)
        assert await has_role("user-1", "contributor") is False


# ---------------------------------------------------------------------------
# create_role_request / list_role_requests
# ---------------------------------------------------------------------------


class TestRoleRequestHelpers:
    @pytest.mark.asyncio
    async def test_create_role_request_returns_int_id(self, db_path: Path):
        await _seed_user(db_path)
        request_id = await create_role_request("user-1", "contributor", user_note="please")
        assert isinstance(request_id, int)
        row = await get_role_request(request_id)
        assert row is not None
        assert row["status"] == "pending"
        assert row["role"] == "contributor"

    @pytest.mark.asyncio
    async def test_list_role_requests_pending_oldest_first(self, db_path: Path):
        await _seed_user(db_path, discord_id="user-1")
        await _seed_user(db_path, discord_id="user-2", name="Other")
        await create_role_request("user-1", "contributor", user_note=None)
        await create_role_request("user-2", "contributor", user_note=None)
        rows = await list_role_requests(status="pending")
        assert rows[0]["id"] <= rows[-1]["id"]  # oldest (lower id) first

    @pytest.mark.asyncio
    async def test_list_role_requests_approved_newest_first(self, db_path: Path):
        await _seed_user(db_path, discord_id="user-1")
        await _seed_user(db_path, discord_id="user-2", name="Other")
        r1_id = await create_role_request("user-1", "contributor", user_note=None)
        r2_id = await create_role_request("user-2", "contributor", user_note=None)
        await review_role_request(r1_id, "approved", "admin-1")
        await review_role_request(r2_id, "approved", "admin-1")
        rows = await list_role_requests(status="approved")
        # newest-first means higher id comes first
        assert rows[0]["id"] >= rows[-1]["id"]


# ---------------------------------------------------------------------------
# review_and_grant_role
# ---------------------------------------------------------------------------


class TestReviewAndGrantRole:
    @pytest.mark.asyncio
    async def test_atomic_approve_and_grant(self, db_path: Path):
        await _seed_user(db_path)
        rr_id = await create_role_request("user-1", "contributor", user_note=None)
        result = await review_and_grant_role(rr_id, "approved", "admin-1")
        assert result is not None
        assert result["status"] == "approved"
        # Role must also be granted
        assert await has_role("user-1", "contributor")

    @pytest.mark.asyncio
    async def test_idempotent_user_already_has_role(self, db_path: Path):
        """If user already has the role, approve still succeeds (INSERT OR IGNORE)."""
        await _seed_user(db_path)
        await grant_role("user-1", "contributor", granted_by="admin-1")
        rr_id = await create_role_request("user-1", "contributor", user_note=None)
        result = await review_and_grant_role(rr_id, "approved", "admin-1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_returns_none_for_already_reviewed_request(self, db_path: Path):
        await _seed_user(db_path)
        rr_id = await create_role_request("user-1", "contributor", user_note=None)
        await review_role_request(rr_id, "rejected", "admin-1")
        # Try to approve now-rejected request
        result = await review_and_grant_role(rr_id, "approved", "admin-1")
        assert result is None


# ---------------------------------------------------------------------------
# withdraw_role_request
# ---------------------------------------------------------------------------


class TestWithdrawRoleRequest:
    @pytest.mark.asyncio
    async def test_withdraw_pending_request(self, db_path: Path):
        await _seed_user(db_path)
        rr_id = await create_role_request("user-1", "contributor", user_note=None)
        assert await withdraw_role_request(rr_id, "user-1") is True

    @pytest.mark.asyncio
    async def test_withdraw_already_approved_returns_false(self, db_path: Path):
        await _seed_user(db_path)
        rr_id = await create_role_request("user-1", "contributor", user_note=None)
        await review_role_request(rr_id, "approved", "admin-1")
        assert await withdraw_role_request(rr_id, "user-1") is False

    @pytest.mark.asyncio
    async def test_withdraw_scoped_to_requester(self, db_path: Path):
        await _seed_user(db_path, discord_id="user-1")
        rr_id = await create_role_request("user-1", "contributor", user_note=None)
        # Different user tries to withdraw
        assert await withdraw_role_request(rr_id, "user-2") is False


# ---------------------------------------------------------------------------
# user_has_capability_via_db / role_has_capability
# ---------------------------------------------------------------------------


class TestCapabilityHelpers:
    @pytest.mark.asyncio
    async def test_user_has_capability_via_granted_role(self, db_path: Path):
        """If the user has a role that maps to a capability, returns True."""
        import aiosqlite

        await _seed_user(db_path)
        # Seed role_permissions directly
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO role_permissions (role, capability) VALUES (?, ?)",
                ("contributor", "edit_zones"),
            )
            await db.commit()
        await grant_role("user-1", "contributor", granted_by="admin-1")
        assert await user_has_capability_via_db("user-1", "edit_zones") is True

    @pytest.mark.asyncio
    async def test_user_lacks_capability_without_role(self, db_path: Path):
        await _seed_user(db_path)
        assert await user_has_capability_via_db("user-1", "edit_zones") is False

    @pytest.mark.asyncio
    async def test_role_has_capability(self, db_path: Path):
        import aiosqlite

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO role_permissions (role, capability) VALUES (?, ?)",
                ("contributor", "edit_raids"),
            )
            await db.commit()
        assert await role_has_capability("contributor", "edit_raids") is True
        assert await role_has_capability("contributor", "nonexistent") is False


# ---------------------------------------------------------------------------
# set_user_access
# ---------------------------------------------------------------------------


class TestSetUserAccess:
    @pytest.mark.asyncio
    async def test_returns_true_on_update(self, db_path: Path):
        await _seed_user(db_path)
        assert await set_user_access("user-1", "approved") is True

    @pytest.mark.asyncio
    async def test_returns_false_for_unknown_user(self, db_path: Path):
        assert await set_user_access("ghost-user", "approved") is False


# ---------------------------------------------------------------------------
# list_role_assignments
# ---------------------------------------------------------------------------


class TestListRoleAssignments:
    @pytest.mark.asyncio
    async def test_returns_mapping_of_user_to_roles(self, db_path: Path):
        await _seed_user(db_path)
        await grant_role("user-1", "contributor", granted_by="admin-1")
        assignments = await list_role_assignments()
        assert "user-1" in assignments
        assert "contributor" in assignments["user-1"]

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_roles(self, db_path: Path):
        await _seed_user(db_path)
        assignments = await list_role_assignments()
        assert "user-1" not in assignments


# ---------------------------------------------------------------------------
# Open signup (OPEN_SIGNUP) — auto-approve + backlog clear
# ---------------------------------------------------------------------------


class TestOpenSignup:
    @pytest.mark.asyncio
    async def test_default_new_user_is_pending(self, db_path: Path):
        status = await upsert_user("u-new", "New", "new", None)
        assert status == "pending"

    @pytest.mark.asyncio
    async def test_open_signup_approves_new_user(self, db_path: Path):
        status = await upsert_user("u-open", "Open", "open", None, open_signup=True)
        assert status == "approved"

    @pytest.mark.asyncio
    async def test_admin_always_approved_even_without_open_signup(self, db_path: Path):
        status = await upsert_user("u-admin", "Admin", "admin", None, admin_ids=frozenset({"u-admin"}))
        assert status == "approved"

    @pytest.mark.asyncio
    async def test_open_signup_does_not_reapprove_on_relogin(self, db_path: Path):
        # First login while signup is closed → pending.
        await upsert_user("u-relog", "Re", "re", None, open_signup=False)
        # Re-login with the flag now ON must NOT auto-approve an existing user;
        # ON CONFLICT preserves stored status (only approve_all_pending does that).
        status = await upsert_user("u-relog", "Re", "re", None, open_signup=True)
        assert status == "pending"

    @pytest.mark.asyncio
    async def test_approve_all_pending_clears_backlog_idempotently(self, db_path: Path):
        await upsert_user("p1", "P1", "p1", None)
        await upsert_user("p2", "P2", "p2", None)
        n = await approve_all_pending()
        assert n == 2
        assert await get_user_access_status("p1") == "approved"
        assert await get_user_access_status("p2") == "approved"
        # Idempotent: nothing pending now.
        assert await approve_all_pending() == 0
