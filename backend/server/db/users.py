"""users.db users table + role / role_request / role_permission helpers.

Carved out of the original 1309-line web/db.py. Async (aiosqlite) helpers
for the users domain. ``path: Path = DB_PATH`` parameter on every public
function so tests can inject a temp DB.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from backend.server.core.sql_helpers import build_where
from backend.server.db import DB_PATH


async def upsert_user(
    discord_id: str,
    discord_name: str,
    discord_username: str,
    avatar: str | None,
    admin_ids: frozenset[str] = frozenset(),
    path: Path = DB_PATH,
) -> str:
    """Insert a new user row or update name/username/avatar and bump last_seen.

    Returns the user's access_status after the upsert.

    Admin IDs (from ADMIN_DISCORD_IDS env var) are always forced to 'approved'
    regardless of what is stored — this prevents admins from being locked out
    even after a complete database wipe.
    """
    is_admin = discord_id in admin_ids
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            INSERT INTO users (discord_id, discord_name, discord_username, avatar, access_status)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                discord_name     = excluded.discord_name,
                discord_username = excluded.discord_username,
                avatar           = excluded.avatar,
                last_seen        = strftime('%s','now'),
                access_status    = CASE
                    WHEN ? = 1 THEN 'approved'
                    ELSE access_status
                END
            """,
            (
                discord_id,
                discord_name,
                discord_username,
                avatar,
                "approved" if is_admin else "pending",
                1 if is_admin else 0,
            ),
        )
        await db.commit()
        async with db.execute("SELECT access_status FROM users WHERE discord_id = ?", (discord_id,)) as cur:
            row = await cur.fetchone()
    return row["access_status"] if row else "pending"


async def get_user_access_status(discord_id: str, path: Path = DB_PATH) -> str:
    """Return the access_status for a user, or 'pending' if not found."""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT access_status FROM users WHERE discord_id = ?", (discord_id,)) as cur:
            row = await cur.fetchone()
    return row["access_status"] if row else "pending"


async def get_display_names_for_discord_ids(ids: list[str], path: Path = DB_PATH) -> dict[str, str]:
    """Return {discord_id: discord_name} for every id present in users.

    Missing/empty input returns {}. Non-discord-id tokens (e.g.
    'eq2i_scrape', 'unknown') are silently absent from the result —
    callers handle the fallback display."""
    if not ids:
        return {}
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        placeholders = ",".join("?" for _ in ids)
        async with db.execute(
            f"SELECT discord_id, discord_name FROM users WHERE discord_id IN ({placeholders})",
            ids,
        ) as cur:
            rows = await cur.fetchall()
    return {r["discord_id"]: r["discord_name"] for r in rows}


async def list_pending_users(path: Path = DB_PATH) -> list[dict]:
    """Return all users with access_status = 'pending', newest first."""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT discord_id, discord_name, discord_username, avatar, first_seen "
            "FROM users WHERE access_status = 'pending' ORDER BY first_seen DESC"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def list_all_users(path: Path = DB_PATH) -> list[dict]:
    """Return all users with access_status and total claim count, newest first."""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT u.discord_id, u.discord_name, u.discord_username, u.avatar,
                   u.first_seen, u.last_seen, u.access_status,
                   COUNT(c.id) AS claim_count
            FROM users u
            LEFT JOIN character_claims c ON c.discord_id = u.discord_id
            GROUP BY u.discord_id
            ORDER BY u.first_seen DESC
            """
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def set_user_access(discord_id: str, status: str, path: Path = DB_PATH) -> bool:
    """Set access_status for a user. Returns True if a row was updated."""
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            "UPDATE users SET access_status = ? WHERE discord_id = ?",
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
    discord_id: str,
    role: str,
    granted_by: str,
    path: Path = DB_PATH,
) -> bool:
    """Grant a role to a user. Idempotent — re-granting an existing (user,
    role) pair is a no-op (returns False). Returns True when a new row was
    actually inserted."""
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO user_roles (discord_id, role, granted_by) VALUES (?, ?, ?)",
            (discord_id, role, granted_by),
        )
        await db.commit()
    return cur.rowcount > 0


async def revoke_role(discord_id: str, role: str, path: Path = DB_PATH) -> bool:
    """Revoke a role. Returns True if a row was deleted (i.e. the user had it)."""
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            "DELETE FROM user_roles WHERE discord_id = ? AND role = ?",
            (discord_id, role),
        )
        await db.commit()
    return cur.rowcount > 0


async def list_roles_for_user(discord_id: str, path: Path = DB_PATH) -> list[str]:
    """All roles assigned to a single user, sorted for stable display."""
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            "SELECT role FROM user_roles WHERE discord_id = ? ORDER BY role",
            (discord_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


async def has_role(discord_id: str, role: str, path: Path = DB_PATH) -> bool:
    """Cheap (indexed) existence check — used on the hot path of the editor
    auth dep, so kept as a single-row SELECT rather than reusing list_roles."""
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            "SELECT 1 FROM user_roles WHERE discord_id = ? AND role = ? LIMIT 1",
            (discord_id, role),
        ) as cur:
            return await cur.fetchone() is not None


async def create_role_request(
    discord_id: str,
    role: str,
    user_note: str | None,
    path: Path = DB_PATH,
) -> int:
    """Submit a pending role request. Raises ``sqlite3.IntegrityError`` if the
    user already has a pending request for this role (the partial unique index
    on (discord_id, role) WHERE status='pending' enforces it). Returns the new
    request's id."""
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            "INSERT INTO role_requests (discord_id, role, user_note) VALUES (?, ?, ?)",
            (discord_id, role, user_note),
        )
        await db.commit()
    return int(cur.lastrowid or 0)


async def list_role_requests(
    *,
    status: str | None = None,
    discord_id: str | None = None,
    path: Path = DB_PATH,
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
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""
            SELECT rr.id, rr.discord_id, rr.role, rr.status,
                   rr.requested_at, rr.reviewed_at, rr.reviewed_by,
                   rr.user_note, rr.admin_note,
                   u.discord_name, u.discord_username, u.avatar
            FROM role_requests rr
            LEFT JOIN users u ON u.discord_id = rr.discord_id
            {where_sql}
            {order_sql}
            """,
            params,
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_role_request(request_id: int, path: Path = DB_PATH) -> dict | None:
    """Single-row fetch, same shape as list_role_requests entries. Used by the
    admin approve/reject endpoints for the 404 check + the role/discord_id
    payload that the grant flow needs."""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT rr.id, rr.discord_id, rr.role, rr.status,
                   rr.requested_at, rr.reviewed_at, rr.reviewed_by,
                   rr.user_note, rr.admin_note,
                   u.discord_name, u.discord_username, u.avatar
            FROM role_requests rr
            LEFT JOIN users u ON u.discord_id = rr.discord_id
            WHERE rr.id = ?
            """,
            (request_id,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def review_role_request(
    request_id: int,
    status: str,
    reviewed_by: str,
    admin_note: str | None = None,
    path: Path = DB_PATH,
) -> dict | None:
    """Approve or reject a pending request. Returns the updated row, or None
    if no pending request with that id exists (already resolved, or unknown)."""
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            """
            UPDATE role_requests SET
                status      = ?,
                reviewed_at = strftime('%s','now'),
                reviewed_by = ?,
                admin_note  = ?
            WHERE id = ? AND status = 'pending'
            """,
            (status, reviewed_by, admin_note, request_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            return None
    return await get_role_request(request_id, path=path)


async def review_and_grant_role(
    request_id: int,
    status: str,
    admin_id: str,
    note: str | None = None,
    path: Path = DB_PATH,
) -> dict | None:
    """Atomically mark a role request approved + insert the user_roles row.

    Single transaction so a process crash between the two writes can't leave
    the queue with a phantom-approved row whose grant never landed. Returns
    the reviewed request dict (or None if not found / already reviewed).

    Idempotency: if the user already holds the role (e.g. admin granted it
    directly between submit + approve), the INSERT OR IGNORE is a no-op and
    the request still transitions to approved.
    """
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            """
            UPDATE role_requests SET
                status      = ?,
                reviewed_at = strftime('%s','now'),
                reviewed_by = ?,
                admin_note  = ?
            WHERE id = ? AND status = 'pending'
            """,
            (status, admin_id, note, request_id),
        )
        if cur.rowcount == 0:
            return None
        # Fetch the request row so we can grant the role
        async with db.execute(
            "SELECT discord_id, role FROM role_requests WHERE id = ?",
            (request_id,),
        ) as sel:
            row = await sel.fetchone()
        if row is not None:
            discord_id, role = row
            await db.execute(
                "INSERT OR IGNORE INTO user_roles (discord_id, role, granted_by) VALUES (?, ?, ?)",
                (discord_id, role, admin_id),
            )
        await db.commit()
    return await get_role_request(request_id, path=path)


async def withdraw_role_request(
    request_id: int,
    discord_id: str,
    path: Path = DB_PATH,
) -> bool:
    """User-initiated cancellation. Scoped to the requester so one user can't
    withdraw another's request. Only pending requests can be withdrawn —
    historical rows stay immutable. Returns True if a row transitioned."""
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            """
            UPDATE role_requests SET status = 'withdrawn'
            WHERE id = ? AND discord_id = ? AND status = 'pending'
            """,
            (request_id, discord_id),
        )
        await db.commit()
    return cur.rowcount > 0


async def user_has_capability_via_db(
    discord_id: str,
    capability: str,
    path: Path = DB_PATH,
) -> bool:
    """True iff any DB-granted role for this user maps to ``capability``.

    Single JOIN'd EXISTS query — indexed on both join keys. Doesn't consider
    admin (synthetic) or officer (dynamic) — those live in the auth dep on
    top of this primitive."""
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            """
            SELECT 1
            FROM user_roles ur
            JOIN role_permissions rp ON rp.role = ur.role
            WHERE ur.discord_id = ? AND rp.capability = ?
            LIMIT 1
            """,
            (discord_id, capability),
        ) as cur:
            return await cur.fetchone() is not None


async def role_has_capability(
    role: str,
    capability: str,
    path: Path = DB_PATH,
) -> bool:
    """True iff ``(role, capability)`` is in role_permissions.

    Used by the auth dep to decide whether to bother running the dynamic
    officer check at all — if officers don't have the capability, no point."""
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            "SELECT 1 FROM role_permissions WHERE role = ? AND capability = ? LIMIT 1",
            (role, capability),
        ) as cur:
            return await cur.fetchone() is not None


async def list_role_assignments(path: Path = DB_PATH) -> dict[str, list[str]]:
    """Return ``{discord_id: [role, …]}`` for every user with at least one role.

    Used by the admin user-list endpoint to join roles in without N+1 queries
    against ``list_roles_for_user``."""
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT discord_id, role FROM user_roles ORDER BY discord_id, role") as cur:
            rows = await cur.fetchall()
    out: dict[str, list[str]] = {}
    for discord_id, role in rows:
        out.setdefault(discord_id, []).append(role)
    return out
