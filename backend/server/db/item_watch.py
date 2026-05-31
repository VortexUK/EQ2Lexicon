"""users.db item_watch table helpers.

Carved out of the original 1309-line web/db.py. Async (aiosqlite) helpers
for the item watch domain. ``path: Path = DB_PATH`` parameter on every
public function so tests can inject a temp DB.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from backend.server.db import DB_PATH


async def add_item_watch(
    guild_name: str,
    character_name: str,
    item_id: int,
    item_name: str,
    added_by: str,
    added_by_name: str,
    world: str = "Varsoon",
    path: Path = DB_PATH,
) -> dict:
    """
    Add a new item watch entry.
    Raises ValueError on duplicate (same world + guild + character + item_id).
    Returns the new row dict.
    """
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        try:
            cur = await db.execute(
                """
                INSERT INTO item_watch
                    (world, guild_name, character_name, item_id, item_name, added_by, added_by_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (world, guild_name, character_name, item_id, item_name, added_by, added_by_name),
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
    world: str = "Varsoon",
    path: Path = DB_PATH,
) -> list[dict]:
    """Return all item watch entries for a guild on a given server, ordered by added_at descending."""
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM item_watch WHERE guild_name = ? AND world = ? ORDER BY added_at DESC",
            (guild_name, world),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def remove_item_watch(
    watch_id: int,
    guild_name: str,
    world: str = "Varsoon",
    path: Path = DB_PATH,
) -> bool:
    """Delete an item watch entry. Scoped to guild_name and world for safety. Returns True if deleted."""
    async with aiosqlite.connect(path) as db:
        cur = await db.execute(
            "DELETE FROM item_watch WHERE id = ? AND guild_name = ? AND world = ?",
            (watch_id, guild_name, world),
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
        sql = f"""
            UPDATE item_watch SET
                last_checked_at = {now},
                last_seen_at    = {now},
                first_seen_at   = COALESCE(first_seen_at, {now})
            WHERE id = ?
            """
    else:
        sql = f"UPDATE item_watch SET last_checked_at = {now} WHERE id = ?"
    async with aiosqlite.connect(path) as db:
        await db.execute(sql, (watch_id,))
        await db.commit()
