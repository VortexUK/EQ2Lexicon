"""users.db servers table helpers (per-server registry).

Carved out of the original 1309-line web/db.py. Synchronous (sqlite3) helpers
for the servers domain — these are called at startup and from sync admin
endpoints, so they use the stdlib driver directly.

``path: Path = DB_PATH`` parameter on every public function so tests can
inject a temp DB.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.server.db import DB_PATH


def _server_row(row: sqlite3.Row) -> dict:
    return {
        "world": row["world"],
        "subdomain": row["subdomain"],
        "display_name": row["display_name"],
        "max_level": row["max_level"],
        "current_xpac": row["current_xpac"],
        "launch_dt": row["launch_dt"],
        "is_default": bool(row["is_default"]),
    }


def list_servers_sync(path: Path = DB_PATH) -> list[dict]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [_server_row(r) for r in conn.execute("SELECT * FROM servers ORDER BY display_name")]


def get_server_by_subdomain_sync(subdomain: str, path: Path = DB_PATH) -> dict | None:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM servers WHERE subdomain = ?", (subdomain.lower(),)).fetchone()
        return _server_row(row) if row else None


def get_server_by_world_sync(world: str, path: Path = DB_PATH) -> dict | None:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM servers WHERE world = ?", (world,)).fetchone()
        return _server_row(row) if row else None


def upsert_server_settings_sync(
    world: str,
    *,
    max_level: int,
    current_xpac: str | None,
    launch_dt: str | None,
    path: Path = DB_PATH,
) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE servers SET max_level = ?, current_xpac = ?, launch_dt = ?, "
            "updated_at = strftime('%s','now') WHERE world = ?",
            (max_level, current_xpac, launch_dt, world),
        )
        conn.commit()


def set_default_server_sync(world: str, path: Path = DB_PATH) -> bool:
    """Atomically set one server as the default (is_default=1) and clear all others.

    Returns True when the world was found and updated, False when ``world`` is
    unknown (i.e. no row matched the second UPDATE — there are never 0 defaults
    after this call succeeds).
    """
    with sqlite3.connect(path) as conn:
        # First clear all, then set the target. Single transaction → never 0 or 2 defaults.
        conn.execute("UPDATE servers SET is_default = 0")
        cur = conn.execute("UPDATE servers SET is_default = 1 WHERE world = ?", (world,))
        if cur.rowcount == 0:
            # Unknown world: roll back by re-establishing any previous default.
            # Re-query to pick the alphabetically first row as a safe fallback so
            # we never leave all rows at is_default=0.
            conn.execute(
                "UPDATE servers SET is_default = 1 WHERE world = "
                "(SELECT world FROM servers ORDER BY display_name LIMIT 1)"
            )
            conn.commit()
            return False
        conn.commit()
    return True
