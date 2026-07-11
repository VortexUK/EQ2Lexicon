"""users.db users table + role / role_request / role_permission helpers.

Carved out of the original 1309-line web/db.py. Async (aiosqlite) helpers
for the users domain. ``path: Path = DB_PATH`` parameter on every public
function so tests can inject a temp DB.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from backend.db_catalogue import AsyncStoreBase
from backend.server.core.sql_helpers import build_where
from backend.server.db import DB_PATH
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


class UsersStore(AsyncStoreBase):
    """users.db `users` domain. Schema/migrations are owned by the package
    orchestrator (backend.server.db.init_db); methods open per-call
    connections against ``self.path``."""

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    async def upsert_user(
        self,
        discord_id: str,
        discord_name: str,
        discord_username: str,
        avatar: str | None,
        admin_ids: frozenset[str] = frozenset(),
        open_signup: bool = False,
    ) -> str:
        """Insert a new user row or update name/username/avatar and bump last_seen.

        Returns the user's access_status after the upsert.

        Admin IDs (from ADMIN_DISCORD_IDS env var) are always forced to 'approved'
        regardless of what is stored — this prevents admins from being locked out
        even after a complete database wipe.

        ``open_signup`` (config.OPEN_SIGNUP) auto-approves brand-new users so they
        skip the admin-approval queue. It only affects the *initial* status on
        insert — re-login preserves whatever is stored (see the ON CONFLICT clause),
        so flipping the flag off later never re-pends an already-approved user.
        """
        is_admin = discord_id in admin_ids
        new_user_status = "approved" if (is_admin or open_signup) else "pending"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                _SQL["upsert_user"],
                (
                    discord_id,
                    discord_name,
                    discord_username,
                    avatar,
                    new_user_status,
                    1 if is_admin else 0,
                ),
            )
            await db.commit()
            async with db.execute(_SQL["select_access_status"], (discord_id,)) as cur:
                row = await cur.fetchone()
        return row["access_status"] if row else "pending"

    async def get_user_access_status(self, discord_id: str) -> str:
        """Return the access_status for a user, or 'pending' if not found."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(_SQL["select_access_status"], (discord_id,)) as cur:
                row = await cur.fetchone()
        return row["access_status"] if row else "pending"

    async def get_display_names_for_discord_ids(self, ids: list[str]) -> dict[str, str]:
        """Return {discord_id: discord_name} for every id present in users.

        Missing/empty input returns {}. Non-discord-id tokens (e.g.
        'eq2i_scrape', 'unknown') are silently absent from the result —
        callers handle the fallback display."""
        if not ids:
            return {}
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            placeholders = ",".join("?" for _ in ids)
            async with db.execute(
                _SQL["select_display_names_by_ids"].format(placeholders=placeholders),
                ids,
            ) as cur:
                rows = await cur.fetchall()
        return {r["discord_id"]: r["discord_name"] for r in rows}

    async def list_pending_users(self) -> list[dict]:
        """Return all users with access_status = 'pending', newest first."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(_SQL["list_pending_users"]) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def approve_all_pending(self) -> int:
        """Flip every access_status='pending' user to 'approved'. Returns the number
        of rows updated. Used at startup when OPEN_SIGNUP is enabled to clear the
        pre-existing approval backlog. Idempotent — a no-op once nothing is pending.
        """
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(_SQL["approve_all_pending"])
            await db.commit()
            return cur.rowcount

    async def list_all_users(self) -> list[dict]:
        """Return all users with access_status and total claim count, newest first."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(_SQL["list_all_users_with_claim_count"]) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def set_user_access(self, discord_id: str, status: str) -> bool:
        """Set access_status for a user. Returns True if a row was updated."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                _SQL["update_user_access_status"],
                (status, discord_id),
            )
            await db.commit()
        return cur.rowcount > 0

    # ---------------------------------------------------------------------------
    # Role helpers
    # ---------------------------------------------------------------------------
    #
    # Role strings are validated at the route layer (against the KNOWN_ROLES set in
    # web/auth_deps.py) — these helpers accept any string so test fixtures and
    # future roles don't need a DB-layer code change.

    async def grant_role(
        self,
        discord_id: str,
        role: str,
        granted_by: str,
    ) -> bool:
        """Grant a role to a user. Idempotent — re-granting an existing (user,
        role) pair is a no-op (returns False). Returns True when a new row was
        actually inserted."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                _SQL["grant_role"],
                (discord_id, role, granted_by),
            )
            await db.commit()
        return cur.rowcount > 0

    async def revoke_role(self, discord_id: str, role: str) -> bool:
        """Revoke a role. Returns True if a row was deleted (i.e. the user had it)."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                _SQL["revoke_role"],
                (discord_id, role),
            )
            await db.commit()
        return cur.rowcount > 0

    async def list_roles_for_user(self, discord_id: str) -> list[str]:
        """All roles assigned to a single user, sorted for stable display."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                _SQL["list_roles_for_user"],
                (discord_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def has_role(self, discord_id: str, role: str) -> bool:
        """Cheap (indexed) existence check — used on the hot path of the editor
        auth dep, so kept as a single-row SELECT rather than reusing list_roles."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                _SQL["check_has_role"],
                (discord_id, role),
            ) as cur:
                return await cur.fetchone() is not None

    async def create_role_request(
        self,
        discord_id: str,
        role: str,
        user_note: str | None,
    ) -> int:
        """Submit a pending role request. Raises ``sqlite3.IntegrityError`` if the
        user already has a pending request for this role (the partial unique index
        on (discord_id, role) WHERE status='pending' enforces it). Returns the new
        request's id."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                _SQL["create_role_request"],
                (discord_id, role, user_note),
            )
            await db.commit()
        return int(cur.lastrowid or 0)

    async def list_role_requests(
        self,
        *,
        status: str | None = None,
        discord_id: str | None = None,
    ) -> list[dict]:
        """List role requests, optionally filtered. Pending sorted oldest-first
        (queue order); everything else newest-first (audit-trail browsing).

        Joins the user table so callers can render the requester without a
        second lookup — admin queue needs the name; user-list of own history
        technically doesn't but the cost is zero."""
        where: list[str] = []
        params: list = []
        if status is not None:
            where.append("rr.status = ?")
            params.append(status)
        if discord_id is not None:
            where.append("rr.discord_id = ?")
            params.append(discord_id)
        where_sql = build_where(where)
        # Pending: oldest first (FIFO queue). Anything resolved: newest first.
        order_sql = (
            "ORDER BY rr.requested_at ASC, rr.id ASC"
            if status == "pending"
            else "ORDER BY rr.requested_at DESC, rr.id DESC"
        )
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                _SQL["list_role_requests"].format(where_sql=where_sql, order_sql=order_sql),
                params,
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_role_request(self, request_id: int) -> dict | None:
        """Single-row fetch, same shape as list_role_requests entries. Used by the
        admin approve/reject endpoints for the 404 check + the role/discord_id
        payload that the grant flow needs."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                _SQL["get_role_request"],
                (request_id,),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def review_role_request(
        self,
        request_id: int,
        status: str,
        reviewed_by: str,
        admin_note: str | None = None,
    ) -> dict | None:
        """Approve or reject a pending request. Returns the updated row, or None
        if no pending request with that id exists (already resolved, or unknown)."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                _SQL["review_role_request"],
                (status, reviewed_by, admin_note, request_id),
            )
            await db.commit()
            if cur.rowcount == 0:
                return None
        return await self.get_role_request(request_id)

    async def review_and_grant_role(
        self,
        request_id: int,
        status: str,
        admin_id: str,
        note: str | None = None,
    ) -> dict | None:
        """Atomically mark a role request approved + insert the user_roles row.

        Single transaction so a process crash between the two writes can't leave
        the queue with a phantom-approved row whose grant never landed. Returns
        the reviewed request dict (or None if not found / already reviewed).

        Idempotency: if the user already holds the role (e.g. admin granted it
        directly between submit + approve), the INSERT OR IGNORE is a no-op and
        the request still transitions to approved.
        """
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                _SQL["review_role_request"],
                (status, admin_id, note, request_id),
            )
            if cur.rowcount == 0:
                return None
            # Fetch the request row so we can grant the role
            async with db.execute(
                _SQL["select_role_request_grant_info"],
                (request_id,),
            ) as sel:
                row = await sel.fetchone()
            if row is not None:
                discord_id, role = row
                await db.execute(
                    _SQL["grant_role"],
                    (discord_id, role, admin_id),
                )
            await db.commit()
        return await self.get_role_request(request_id)

    async def withdraw_role_request(
        self,
        request_id: int,
        discord_id: str,
    ) -> bool:
        """User-initiated cancellation. Scoped to the requester so one user can't
        withdraw another's request. Only pending requests can be withdrawn —
        historical rows stay immutable. Returns True if a row transitioned."""
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                _SQL["withdraw_role_request"],
                (request_id, discord_id),
            )
            await db.commit()
        return cur.rowcount > 0

    async def user_has_capability_via_db(
        self,
        discord_id: str,
        capability: str,
    ) -> bool:
        """True iff any DB-granted role for this user maps to ``capability``.

        Single JOIN'd EXISTS query — indexed on both join keys. Doesn't consider
        admin (synthetic) or officer (dynamic) — those live in the auth dep on
        top of this primitive."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                _SQL["check_user_has_capability"],
                (discord_id, capability),
            ) as cur:
                return await cur.fetchone() is not None

    async def role_has_capability(
        self,
        role: str,
        capability: str,
    ) -> bool:
        """True iff ``(role, capability)`` is in role_permissions.

        Used by the auth dep to decide whether to bother running the dynamic
        officer check at all — if officers don't have the capability, no point."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                _SQL["check_role_has_capability"],
                (role, capability),
            ) as cur:
                return await cur.fetchone() is not None

    async def list_role_assignments(self) -> dict[str, list[str]]:
        """Return ``{discord_id: [role, …]}`` for every user with at least one role.

        Used by the admin user-list endpoint to join roles in without N+1 queries
        against ``list_roles_for_user``."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(_SQL["list_all_role_assignments"]) as cur:
                rows = await cur.fetchall()
        out: dict[str, list[str]] = {}
        for discord_id, role in rows:
            out.setdefault(discord_id, []).append(role)
        return out


# The shared default instance — every runtime consumer goes through this.
store = UsersStore()
