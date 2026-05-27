"""Async SQLite layer for users and character_claims tables.

Separate from the item catalogue DB (census/db.py).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from pathlib import Path

import aiosqlite

# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    env = os.getenv("DB_USERS_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "users.db"


DB_PATH = _db_path()

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    discord_id       TEXT PRIMARY KEY,
    discord_name     TEXT NOT NULL,
    discord_username TEXT,
    avatar           TEXT,
    first_seen       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    last_seen        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    access_status    TEXT    NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS character_claims (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id      TEXT    NOT NULL REFERENCES users(discord_id),
    character_name  TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending',
    requested_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    reviewed_at     INTEGER,
    reviewed_by     TEXT,
    note            TEXT
);

CREATE INDEX IF NOT EXISTS idx_claims_discord ON character_claims(discord_id);
CREATE INDEX IF NOT EXISTS idx_claims_status  ON character_claims(status);

CREATE TABLE IF NOT EXISTS item_watch (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_name      TEXT    NOT NULL,
    character_name  TEXT    NOT NULL,
    item_id         INTEGER NOT NULL,
    item_name       TEXT    NOT NULL,
    added_by        TEXT    NOT NULL REFERENCES users(discord_id),
    added_by_name   TEXT    NOT NULL,
    added_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    first_seen_at   INTEGER,        -- first time we saw them wearing it (NULL = never)
    last_seen_at    INTEGER,        -- most recent check where they had it equipped
    last_checked_at INTEGER,        -- most recent check (any result)
    UNIQUE(guild_name, character_name, item_id)
);

CREATE INDEX IF NOT EXISTS idx_watch_guild ON item_watch(guild_name);

-- Persistent, admin-grantable roles.
--
-- The role-source layering for content-edit gates is:
--   admin       — env-driven (ADMIN_DISCORD_IDS). Stays out of this table so
--                 a DB wipe can't lock admins out.
--   contributor — DB-driven via this table (admin-grantable from the UI).
--   officer     — dynamic, computed from Census guild rank at request time;
--                 never persisted here.
--
-- One row per (user, role) pair. Adding a new role to the system is data, not
-- schema — just start inserting rows under a new role name and gate it where
-- appropriate.
--
-- TODO(future): per-role permission system. Today the role → capability
-- mapping is hardcoded inside `require_editor` (and any future require_X).
-- When the codebase grows >1 capability dimensions, layer a `role_permissions`
-- table (role TEXT, capability TEXT) on top of this and have the deps consult
-- it instead of hardcoded role names. Until then YAGNI.
--
CREATE TABLE IF NOT EXISTS user_roles (
    discord_id  TEXT    NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
    role        TEXT    NOT NULL,
    granted_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    granted_by  TEXT    NOT NULL,           -- discord_id of the granting admin
    PRIMARY KEY (discord_id, role)
);

CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role);

-- Per-role capability map. The route-layer auth gate (`require_capability`
-- in web/auth_deps.py) JOINs user_roles ↔ role_permissions on `role` to
-- answer "does this user have capability X?".
--
-- Admin is the synthetic "all capabilities" branch and never appears here.
-- Officer DOES appear here even though it's not stored in user_roles — the
-- dep dynamically resolves officer status when it sees an ('officer', X)
-- row and the user lacks the capability via DB roles. That keeps adding a
-- new capability for officer a one-row INSERT rather than a code change.
--
-- New capability = INSERT rows here (admins/contributors/officers as
-- appropriate). Re-seeding is idempotent via INSERT OR IGNORE in
-- ``init_db``.
CREATE TABLE IF NOT EXISTS role_permissions (
    role        TEXT NOT NULL,
    capability  TEXT NOT NULL,
    PRIMARY KEY (role, capability)
);

CREATE INDEX IF NOT EXISTS idx_role_permissions_capability
    ON role_permissions(capability);

-- Self-service role requests. Mirrors the character_claims queue pattern:
-- users submit, admin reviews, request transitions through statuses. On
-- approval the route also writes a row into user_roles so the request +
-- the grant are decoupled (an approved request is immutable history; the
-- grant can be independently revoked).
--
-- Status transitions:
--   pending     — submitted by the user, awaiting admin review
--   approved    — admin approved + user_roles row inserted
--   rejected    — admin rejected (admin_note may carry the reason)
--   withdrawn   — user cancelled their own pending request
CREATE TABLE IF NOT EXISTS role_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id   TEXT    NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
    role         TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    requested_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    reviewed_at  INTEGER,
    reviewed_by  TEXT,                                 -- admin's discord_id
    user_note    TEXT,                                 -- "why I want this" note
    admin_note   TEXT                                  -- admin's response note
);

CREATE INDEX IF NOT EXISTS idx_role_requests_status  ON role_requests(status);
CREATE INDEX IF NOT EXISTS idx_role_requests_discord ON role_requests(discord_id);

-- Only one pending request per (user, role) — a second submit while one's
-- in flight is rejected by the route. Resolved requests (approved/rejected/
-- withdrawn) can coexist for the same (user, role) for the audit trail.
CREATE UNIQUE INDEX IF NOT EXISTS idx_role_requests_one_pending
    ON role_requests(discord_id, role) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS api_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT    NOT NULL REFERENCES users(discord_id),
    name            TEXT    NOT NULL,           -- user-given label e.g. "Desktop ACT"
    token_hash      TEXT    NOT NULL UNIQUE,    -- sha256 hex of the raw token
    token_prefix    TEXT    NOT NULL,           -- first 12 chars for UI display (eq2c_ + 7 chars)
    created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    last_used_at    INTEGER,                    -- updated on each successful auth
    revoked_at      INTEGER                     -- non-NULL = inactive
);

CREATE INDEX IF NOT EXISTS idx_tokens_user ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_tokens_hash ON api_tokens(token_hash);
"""

# Claim statuses:
#   pending    – submitted, awaiting admin review
#   approved   – admin approved; user's active character
#   rejected   – admin rejected (note may contain reason)
#   withdrawn  – user cancelled their own pending request
#   superseded – was approved but user submitted a new claim that was later approved


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


def init_db(path: Path = DB_PATH) -> None:
    """Create tables if they don't exist, and migrate existing ones.  Called once at startup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        # Migrate: add columns introduced after initial schema
        users_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "discord_username" not in users_cols:
            conn.execute("ALTER TABLE users ADD COLUMN discord_username TEXT")
        if "access_status" not in users_cols:
            conn.execute("ALTER TABLE users ADD COLUMN access_status TEXT NOT NULL DEFAULT 'pending'")
            # Existing users were already using the app — approve them all.
            # Only brand-new users (after this migration) start as 'pending'.
            conn.execute("UPDATE users SET access_status = 'approved'")
        claims_cols = {row[1] for row in conn.execute("PRAGMA table_info(character_claims)")}
        if "is_primary" not in claims_cols:
            conn.execute("ALTER TABLE character_claims ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0")

        # Seed role_permissions. INSERT OR IGNORE keeps it idempotent and
        # leaves any admin-edited rows alone if/when a future UI exposes the
        # table for live edits. Listed here (not in _SCHEMA) so the seed
        # statements live with their semantic intent.
        conn.executemany(
            "INSERT OR IGNORE INTO role_permissions (role, capability) VALUES (?, ?)",
            [
                ("contributor", "edit_content"),
                ("officer", "edit_content"),
            ],
        )
        conn.commit()


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------


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
    where_sql = "WHERE " + " AND ".join(where) if where else ""
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


# ---------------------------------------------------------------------------
# Claim helpers
# ---------------------------------------------------------------------------


async def get_active_claims(
    discord_id: str,
    path: Path = DB_PATH,
) -> dict:
    """
    Return all active claims for this user as:
      { 'approved': [list of approved claim dicts], 'pending': claim dict or None }
    Ignores withdrawn / rejected / superseded.
    """
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM character_claims
            WHERE discord_id = ? AND status IN ('approved', 'pending')
            ORDER BY requested_at ASC
            """,
            (discord_id,),
        ) as cur:
            rows = await cur.fetchall()
    claims = [dict(r) for r in rows]
    return {
        "approved": [c for c in claims if c["status"] == "approved"],
        "pending": next((c for c in claims if c["status"] == "pending"), None),
    }


async def submit_claim(
    discord_id: str,
    character_name: str,
    path: Path = DB_PATH,
) -> dict:
    """
    Cancel any current *pending* claim for this user (set to 'withdrawn'),
    then insert a new pending claim.  Returns the new claim dict.
    Already-approved claims for other characters are not affected.
    Raises ValueError if this character is already approved for this user.
    """
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        # Reject if this character is already claimed (approved or pending) by anyone
        async with db.execute(
            "SELECT discord_id FROM character_claims WHERE character_name = ? AND status IN ('approved', 'pending')",
            (character_name,),
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            if existing["discord_id"] == discord_id:
                raise ValueError(f"'{character_name}' is already claimed on your account.")
            else:
                raise ValueError(f"'{character_name}' has already been claimed by another player.")
        # Cancel any existing pending claim (one pending at a time)
        await db.execute(
            "UPDATE character_claims SET status = 'withdrawn' WHERE discord_id = ? AND status = 'pending'",
            (discord_id,),
        )
        cur = await db.execute(
            "INSERT INTO character_claims (discord_id, character_name, status) VALUES (?, ?, 'pending')",
            (discord_id, character_name),
        )
        new_id = cur.lastrowid
        await db.commit()
        async with db.execute("SELECT * FROM character_claims WHERE id = ?", (new_id,)) as cur2:
            row = await cur2.fetchone()
    assert row is not None, "INSERT succeeded but SELECT returned nothing"
    return dict(row)


async def withdraw_claim(
    claim_id: int,
    discord_id: str,
    path: Path = DB_PATH,
) -> bool:
    """
    Withdraw or remove a specific claim belonging to this user.
    Works on both pending and approved claims.
    If the withdrawn claim was the primary, the oldest remaining approved
    character is automatically promoted to primary.
    Returns True if something changed.
    """
    async with aiosqlite.connect(path) as db:
        # Check if this claim is primary before withdrawing
        async with db.execute(
            "SELECT is_primary FROM character_claims WHERE id = ? AND discord_id = ?",
            (claim_id, discord_id),
        ) as cur:
            row = await cur.fetchone()
        was_primary = row is not None and row[0] == 1

        cur = await db.execute(
            "UPDATE character_claims SET status = 'withdrawn', is_primary = 0 "
            "WHERE id = ? AND discord_id = ? AND status IN ('pending', 'approved')",
            (claim_id, discord_id),
        )
        changed = cur.rowcount > 0

        # Promote the oldest remaining approved character to primary
        if changed and was_primary:
            await db.execute(
                """
                UPDATE character_claims SET is_primary = 1
                WHERE id = (
                    SELECT id FROM character_claims
                    WHERE discord_id = ? AND status = 'approved' AND id != ?
                    ORDER BY requested_at ASC
                    LIMIT 1
                )
                """,
                (discord_id, claim_id),
            )

        await db.commit()
    return changed


async def set_primary(
    discord_id: str,
    claim_id: int,
    path: Path = DB_PATH,
) -> bool:
    """
    Set a specific approved claim as the user's primary character.
    Clears is_primary on all other approved claims for this user.
    Returns True if the target claim exists and was updated.
    """
    async with aiosqlite.connect(path) as db:
        # Verify the claim belongs to this user and is approved
        async with db.execute(
            "SELECT id FROM character_claims WHERE id = ? AND discord_id = ? AND status = 'approved'",
            (claim_id, discord_id),
        ) as cur:
            if not await cur.fetchone():
                return False

        # Clear primary on all approved claims for this user, then set on target
        await db.execute(
            "UPDATE character_claims SET is_primary = 0 WHERE discord_id = ? AND status = 'approved'",
            (discord_id,),
        )
        await db.execute(
            "UPDATE character_claims SET is_primary = 1 WHERE id = ?",
            (claim_id,),
        )
        await db.commit()
    return True


async def get_claim_by_id(
    claim_id: int,
    path: Path = DB_PATH,
) -> dict | None:
    """Return a single claim joined with its submitting user's info."""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT c.*, u.discord_name, u.discord_username, u.avatar
            FROM character_claims c
            LEFT JOIN users u ON u.discord_id = c.discord_id
            WHERE c.id = ?
            """,
            (claim_id,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def list_claims(
    status: str | None = None,
    path: Path = DB_PATH,
) -> list[dict]:
    """
    List claims joined with user info.
    Pending claims are sorted oldest-first (queue order).
    All other statuses are sorted newest-first.
    """
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        if status:
            order = "ASC" if status == "pending" else "DESC"
            async with db.execute(
                f"""
                SELECT c.*, u.discord_name, u.discord_username, u.avatar
                FROM character_claims c
                LEFT JOIN users u ON u.discord_id = c.discord_id
                WHERE c.status = ?
                ORDER BY c.requested_at {order}
                """,
                (status,),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                """
                SELECT c.*, u.discord_name, u.discord_username, u.avatar
                FROM character_claims c
                LEFT JOIN users u ON u.discord_id = c.discord_id
                ORDER BY c.requested_at DESC
                """
            ) as cur:
                rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def review_claim(
    claim_id: int,
    status: str,
    admin_id: str,
    note: str | None = None,
    path: Path = DB_PATH,
) -> dict | None:
    """
    Approve or reject a claim.
    On approval, any previously-approved claims for the same user are
    set to 'superseded' so there is at most one approved claim per user.
    Returns the updated claim (with user info) or None if not found.
    """
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT discord_id FROM character_claims WHERE id = ?", (claim_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        discord_id = row[0]

        await db.execute(
            """
            UPDATE character_claims
            SET status = ?,
                reviewed_at = strftime('%s','now'),
                reviewed_by = ?,
                note = ?
            WHERE id = ?
            """,
            (status, admin_id, note, claim_id),
        )
        # Auto-assign primary if this is the user's first approved character
        if status == "approved":
            async with db.execute(
                "SELECT id FROM character_claims "
                "WHERE discord_id = ? AND status = 'approved' AND is_primary = 1 AND id != ?",
                (discord_id, claim_id),
            ) as cur:
                has_primary = await cur.fetchone() is not None
            if not has_primary:
                await db.execute(
                    "UPDATE character_claims SET is_primary = 1 WHERE id = ?",
                    (claim_id,),
                )
        await db.commit()

    return await get_claim_by_id(claim_id, path)


async def delete_claim(
    claim_id: int,
    path: Path = DB_PATH,
) -> bool:
    """Hard-delete a claim row. Returns True if a row was deleted."""
    async with aiosqlite.connect(path) as db:
        cur = await db.execute("DELETE FROM character_claims WHERE id = ?", (claim_id,))
        deleted = cur.rowcount > 0
        await db.commit()
    return deleted


async def delete_claims_for_user(
    discord_id: str,
    path: Path = DB_PATH,
) -> int:
    """Hard-delete all claim rows for a user. Returns the number of rows deleted."""
    async with aiosqlite.connect(path) as db:
        cur = await db.execute("DELETE FROM character_claims WHERE discord_id = ?", (discord_id,))
        count = cur.rowcount
        await db.commit()
    return count


# ---------------------------------------------------------------------------
# Item watch helpers
# ---------------------------------------------------------------------------


async def add_item_watch(
    guild_name: str,
    character_name: str,
    item_id: int,
    item_name: str,
    added_by: str,
    added_by_name: str,
    path: Path = DB_PATH,
) -> dict:
    """
    Add a new item watch entry.
    Raises ValueError on duplicate (same guild + character + item_id).
    Returns the new row dict.
    """
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        try:
            cur = await db.execute(
                """
                INSERT INTO item_watch
                    (guild_name, character_name, item_id, item_name, added_by, added_by_name)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_name, character_name, item_id, item_name, added_by, added_by_name),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise ValueError(f"'{item_name}' is already being watched for {character_name}.") from exc
            raise
        new_id = cur.lastrowid
        await db.commit()
        async with db.execute("SELECT * FROM item_watch WHERE id = ?", (new_id,)) as cur2:
            row = await cur2.fetchone()
    assert row is not None, "INSERT succeeded but SELECT returned nothing"
    return dict(row)


async def list_item_watches(
    guild_name: str,
    path: Path = DB_PATH,
) -> list[dict]:
    """Return all item watch entries for a guild, ordered by added_at descending."""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM item_watch WHERE guild_name = ? ORDER BY added_at DESC",
            (guild_name,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def remove_item_watch(
    watch_id: int,
    guild_name: str,
    path: Path = DB_PATH,
) -> bool:
    """Delete an item watch entry. Scoped to guild_name for safety. Returns True if deleted."""
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            "DELETE FROM item_watch WHERE id = ? AND guild_name = ?",
            (watch_id, guild_name),
        )
        deleted = cur.rowcount > 0
        await db.commit()
    return deleted


async def update_item_watch_check(
    watch_id: int,
    seen: bool,
    path: Path = DB_PATH,
) -> None:
    """
    Record the result of an equipment check.
    Updates last_checked_at always.
    Updates last_seen_at (and first_seen_at if not yet set) only when seen=True.
    """
    now = "strftime('%s','now')"
    if seen:
        await _run(
            path,
            f"""
            UPDATE item_watch SET
                last_checked_at = {now},
                last_seen_at    = {now},
                first_seen_at   = COALESCE(first_seen_at, {now})
            WHERE id = ?
            """,
            (watch_id,),
        )
    else:
        await _run(
            path,
            f"UPDATE item_watch SET last_checked_at = {now} WHERE id = ?",
            (watch_id,),
        )


async def _run(path: Path, sql: str, params: tuple = ()) -> None:
    """Execute a single write statement."""
    async with aiosqlite.connect(path) as db:
        await db.execute(sql, params)
        await db.commit()


# ---------------------------------------------------------------------------
# API tokens — bearer auth for the ACT plugin (and any future CLI/sidecar).
# Raw tokens are 'eq2c_' + 32 url-safe base64 chars (≈192 bits entropy).
# Only the SHA-256 hash is stored; the raw token is shown to the user once
# at mint time and never recoverable.
# ---------------------------------------------------------------------------

TOKEN_PREFIX = "eq2c_"


def generate_token() -> tuple[str, str, str]:
    """Mint a new bearer token.

    Returns (raw_token, sha256_hex, prefix_for_display).
    The raw_token is what the user pastes into the plugin — show it ONCE.
    """
    body = secrets.token_urlsafe(24)  # ~32 char url-safe base64
    raw = f"{TOKEN_PREFIX}{body}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    prefix = raw[:12]  # eq2c_ + 7 chars — enough to disambiguate in UI
    return raw, h, prefix


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def mint_api_token(
    user_id: str,
    name: str,
    path: Path = DB_PATH,
) -> tuple[str, dict]:
    """Create a new token row. Returns (raw_token, row_dict).

    The raw_token must be returned to the caller and shown to the user
    immediately — it cannot be recovered later.
    """
    raw, h, prefix = generate_token()
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            INSERT INTO api_tokens (user_id, name, token_hash, token_prefix)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, name, h, prefix),
        )
        new_id = cur.lastrowid
        await db.commit()
        async with db.execute("SELECT * FROM api_tokens WHERE id = ?", (new_id,)) as cur2:
            row = await cur2.fetchone()
    assert row is not None
    return raw, dict(row)


async def list_api_tokens(user_id: str, path: Path = DB_PATH) -> list[dict]:
    """All tokens for a user, newest first. Hash is omitted — UI doesn't need it."""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, name, token_prefix, created_at, last_used_at, revoked_at
            FROM api_tokens
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def revoke_api_token(
    user_id: str,
    token_id: int,
    path: Path = DB_PATH,
) -> bool:
    """Mark a token revoked. Scoped to user_id so one user can't revoke another's.
    Returns True if a row was updated."""
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            """
            UPDATE api_tokens
            SET revoked_at = strftime('%s','now')
            WHERE id = ? AND user_id = ? AND revoked_at IS NULL
            """,
            (token_id, user_id),
        )
        await db.commit()
    return cur.rowcount > 0


async def lookup_api_token(raw_token: str, path: Path = DB_PATH) -> dict | None:
    """Look up a token by its raw value (we hash internally). Returns the
    row plus the joined user info, or None if not found / revoked / expired.
    Side effect: bumps last_used_at on success."""
    if not raw_token or not raw_token.startswith(TOKEN_PREFIX):
        return None
    h = hash_token(raw_token)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT t.id AS token_id, t.user_id, t.name AS token_name, t.revoked_at,
                   u.discord_id, u.discord_name, u.discord_username, u.avatar,
                   u.access_status
            FROM api_tokens t
            JOIN users u ON u.discord_id = t.user_id
            WHERE t.token_hash = ?
            """,
            (h,),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        # Bump last_used_at — fire and forget, don't fail the auth on this.
        await db.execute(
            "UPDATE api_tokens SET last_used_at = strftime('%s','now') WHERE id = ?",
            (row["token_id"],),
        )
        await db.commit()
    return dict(row)
