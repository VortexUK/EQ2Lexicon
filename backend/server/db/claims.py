"""users.db character_claims table helpers.

Carved out of the original 1309-line web/db.py. Async (aiosqlite) helpers
for the character claims domain. ``path: Path = DB_PATH`` parameter on every
public function so tests can inject a temp DB.

Claim statuses:
  pending    – submitted, awaiting admin review
  approved   – admin approved; user's active character
  rejected   – admin rejected (note may contain reason)
  withdrawn  – user cancelled their own pending request
  superseded – was approved but user submitted a new claim that was later approved
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from backend.server.db import DB_PATH


async def get_active_claims(
    discord_id: str,
    world: str = "Varsoon",
    path: Path = DB_PATH,
) -> dict:
    """
    Return all active claims for this user on the given world as:
      { 'approved': [list of approved claim dicts], 'pending': claim dict or None }
    Ignores withdrawn / rejected / superseded.
    Claims are scoped to (discord_id, world) — a user's Varsoon and Wuoshi
    primaries are completely independent.
    """
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM character_claims
            WHERE discord_id = ? AND world = ? AND status IN ('approved', 'pending')
            ORDER BY requested_at ASC, id ASC
            """,
            (discord_id, world),
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
    world: str = "Varsoon",
    path: Path = DB_PATH,
) -> dict:
    """
    Cancel any current *pending* claim for this user on this world (set to
    'withdrawn'), then insert a new pending claim.  Returns the new claim dict.
    Already-approved claims for other characters are not affected.
    Raises ValueError if this character is already claimed on this world.
    """
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        # Reject if this character name is already claimed (approved or pending)
        # by anyone *on this world* (EQ2 names are unique only within a server)
        async with db.execute(
            "SELECT discord_id FROM character_claims "
            "WHERE character_name = ? AND world = ? AND status IN ('approved', 'pending')",
            (character_name, world),
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            if existing["discord_id"] == discord_id:
                raise ValueError(f"'{character_name}' is already claimed on your account.")
            else:
                raise ValueError(f"'{character_name}' has already been claimed by another player.")
        # Cancel any existing pending claim on this world (one pending per world at a time)
        await db.execute(
            "UPDATE character_claims SET status = 'withdrawn' WHERE discord_id = ? AND world = ? AND status = 'pending'",
            (discord_id, world),
        )
        cur = await db.execute(
            "INSERT INTO character_claims (discord_id, character_name, status, world) VALUES (?, ?, 'pending', ?)",
            (discord_id, character_name, world),
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
    world: str = "Varsoon",
    path: Path = DB_PATH,
) -> bool:
    """
    Withdraw or remove a specific claim belonging to this user.
    Works on both pending and approved claims.
    If the withdrawn claim was the primary, the oldest remaining approved
    character on the same world is automatically promoted to primary.
    Returns True if something changed.
    """
    async with aiosqlite.connect(path) as db:
        # Check if this claim is primary before withdrawing (and capture world from row)
        async with db.execute(
            "SELECT is_primary, world FROM character_claims WHERE id = ? AND discord_id = ?",
            (claim_id, discord_id),
        ) as cur:
            row = await cur.fetchone()
        was_primary = row is not None and row[0] == 1
        claim_world = row[1] if row is not None else world

        cur = await db.execute(
            "UPDATE character_claims SET status = 'withdrawn', is_primary = 0 "
            "WHERE id = ? AND discord_id = ? AND status IN ('pending', 'approved')",
            (claim_id, discord_id),
        )
        changed = cur.rowcount > 0

        # Promote the oldest remaining approved character on the same world to primary
        if changed and was_primary:
            await db.execute(
                """
                UPDATE character_claims SET is_primary = 1
                WHERE id = (
                    SELECT id FROM character_claims
                    WHERE discord_id = ? AND world = ? AND status = 'approved' AND id != ?
                    ORDER BY requested_at ASC
                    LIMIT 1
                )
                """,
                (discord_id, claim_world, claim_id),
            )

        await db.commit()
    return changed


async def set_primary(
    discord_id: str,
    claim_id: int,
    world: str = "Varsoon",
    path: Path = DB_PATH,
) -> bool:
    """
    Set a specific approved claim as the user's primary character on the given world.
    Clears is_primary on all other approved claims for this user on the same world.
    Claims on other worlds are not affected.
    Returns True if the target claim exists and was updated.
    """
    async with aiosqlite.connect(path) as db:
        # Verify the claim belongs to this user, is approved, and is on the right world
        async with db.execute(
            "SELECT id FROM character_claims WHERE id = ? AND discord_id = ? AND world = ? AND status = 'approved'",
            (claim_id, discord_id, world),
        ) as cur:
            if not await cur.fetchone():
                return False

        # Clear primary on all approved claims for this user on this world, then set on target
        await db.execute(
            "UPDATE character_claims SET is_primary = 0 WHERE discord_id = ? AND world = ? AND status = 'approved'",
            (discord_id, world),
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
    world: str | None = None,
    path: Path = DB_PATH,
) -> list[dict]:
    """
    List claims joined with user info, optionally filtered by status and/or world.
    world=None returns claims from all worlds (admin overview).
    world='Varsoon' (or any other) scopes to that server only.
    Pending claims are sorted oldest-first (queue order).
    All other statuses are sorted newest-first.
    """
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        where_parts: list[str] = []
        params: list = []
        if status:
            where_parts.append("c.status = ?")
            params.append(status)
        if world is not None:
            where_parts.append("c.world = ?")
            params.append(world)
        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        order = "ASC" if status == "pending" else "DESC"
        async with db.execute(
            f"""
            SELECT c.*, u.discord_name, u.discord_username, u.avatar
            FROM character_claims c
            LEFT JOIN users u ON u.discord_id = c.discord_id
            {where_sql}
            ORDER BY c.requested_at {order}
            """,
            params,
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
    On approval, auto-assigns is_primary if this is the user's first approved
    character on this world (scoped to (discord_id, world) so each server has
    an independent primary).
    Returns the updated claim (with user info) or None if not found.
    """
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT discord_id, world FROM character_claims WHERE id = ?", (claim_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        discord_id = row[0]
        claim_world = row[1]

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
        # Auto-assign primary if this is the user's first approved character on this world
        if status == "approved":
            async with db.execute(
                "SELECT id FROM character_claims "
                "WHERE discord_id = ? AND world = ? AND status = 'approved' AND is_primary = 1 AND id != ?",
                (discord_id, claim_world, claim_id),
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
