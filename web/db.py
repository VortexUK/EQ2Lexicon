"""Async SQLite layer for users and character_claims tables.

Separate from the item catalogue DB (census/db.py).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import aiosqlite


# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    env = os.getenv("USERS_DB_PATH")
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
    last_seen        INTEGER NOT NULL DEFAULT (strftime('%s','now'))
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
        claims_cols = {row[1] for row in conn.execute("PRAGMA table_info(character_claims)")}
        if "is_primary" not in claims_cols:
            conn.execute("ALTER TABLE character_claims ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0")
        conn.commit()


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

async def upsert_user(
    discord_id: str,
    discord_name: str,
    discord_username: str,
    avatar: str | None,
    path: Path = DB_PATH,
) -> None:
    """Insert a new user row or update name/username/avatar and bump last_seen."""
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            INSERT INTO users (discord_id, discord_name, discord_username, avatar)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                discord_name     = excluded.discord_name,
                discord_username = excluded.discord_username,
                avatar           = excluded.avatar,
                last_seen        = strftime('%s','now')
            """,
            (discord_id, discord_name, discord_username, avatar),
        )
        await db.commit()


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
        "pending":  next((c for c in claims if c["status"] == "pending"), None),
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
        # Reject duplicates — character already approved for this user
        async with db.execute(
            "SELECT id FROM character_claims "
            "WHERE discord_id = ? AND character_name = ? AND status = 'approved'",
            (discord_id, character_name),
        ) as cur:
            if await cur.fetchone():
                raise ValueError(f"'{character_name}' is already an approved character on your account.")
        # Cancel any existing pending claim (one pending at a time)
        await db.execute(
            "UPDATE character_claims SET status = 'withdrawn' "
            "WHERE discord_id = ? AND status = 'pending'",
            (discord_id,),
        )
        cur = await db.execute(
            "INSERT INTO character_claims (discord_id, character_name, status) "
            "VALUES (?, ?, 'pending')",
            (discord_id, character_name),
        )
        new_id = cur.lastrowid
        await db.commit()
        async with db.execute(
            "SELECT * FROM character_claims WHERE id = ?", (new_id,)
        ) as cur2:
            row = await cur2.fetchone()
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
            SELECT c.*, u.discord_name, u.avatar
            FROM character_claims c
            JOIN users u ON u.discord_id = c.discord_id
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
                SELECT c.*, u.discord_name, u.avatar
                FROM character_claims c
                JOIN users u ON u.discord_id = c.discord_id
                WHERE c.status = ?
                ORDER BY c.requested_at {order}
                """,
                (status,),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                """
                SELECT c.*, u.discord_name, u.avatar
                FROM character_claims c
                JOIN users u ON u.discord_id = c.discord_id
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
        async with db.execute(
            "SELECT discord_id FROM character_claims WHERE id = ?", (claim_id,)
        ) as cur:
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
        cur = await db.execute(
            "DELETE FROM character_claims WHERE id = ?", (claim_id,)
        )
        deleted = cur.rowcount > 0
        await db.commit()
    return deleted


async def delete_claims_for_user(
    discord_id: str,
    path: Path = DB_PATH,
) -> int:
    """Hard-delete all claim rows for a user. Returns the number of rows deleted."""
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            "DELETE FROM character_claims WHERE discord_id = ?", (discord_id,)
        )
        count = cur.rowcount
        await db.commit()
    return count
