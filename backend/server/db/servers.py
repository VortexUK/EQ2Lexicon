"""users.db servers table helpers (per-server registry).

Carved out of the original 1309-line web/db.py. Synchronous (sqlite3) helpers
for the servers domain — these are called at startup and from sync admin
endpoints, so they use the stdlib driver directly.

Tests re-point ``store.path`` or construct ``ServersStore(tmp_db)``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.db_catalogue import PathBound
from backend.server.db import DB_PATH
from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


class ServersStore(PathBound):
    """users.db `servers` domain. Schema/migrations are owned by the package
    orchestrator (backend.server.db.init_db); methods open per-call
    connections against ``self.path``."""

    def __init__(self, path: Path = DB_PATH) -> None:
        super().__init__(path)

    @staticmethod
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

    def list_servers_sync(self) -> list[dict]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            return [ServersStore._server_row(r) for r in conn.execute(_SQL["list_all"])]

    def get_server_by_subdomain_sync(self, subdomain: str) -> dict | None:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(_SQL["find_by_subdomain"], (subdomain.lower(),)).fetchone()
            return ServersStore._server_row(row) if row else None

    def get_server_by_world_sync(self, world: str) -> dict | None:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(_SQL["find_by_world"], (world,)).fetchone()
            return ServersStore._server_row(row) if row else None

    def upsert_server_settings_sync(
        self,
        world: str,
        *,
        max_level: int,
        current_xpac: str | None,
        launch_dt: str | None,
    ) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                _SQL["upsert_server_settings"],
                (max_level, current_xpac, launch_dt, world),
            )
            conn.commit()

    def set_default_server_sync(self, world: str) -> bool:
        """Atomically set one server as the default (is_default=1) and clear all others.

        Returns True when the world was found and updated, False when ``world`` is
        unknown (i.e. no row matched the second UPDATE — there are never 0 defaults
        after this call succeeds).
        """
        with sqlite3.connect(self.path) as conn:
            # First clear all, then set the target. Single transaction → never 0 or 2 defaults.
            conn.execute(_SQL["clear_all_defaults"])
            cur = conn.execute(_SQL["set_default_by_world"], (world,))
            if cur.rowcount == 0:
                # Unknown world: roll back by re-establishing any previous default.
                # Re-query to pick the alphabetically first row as a safe fallback so
                # we never leave all rows at is_default=0.
                conn.execute(_SQL["set_default_fallback"])
                conn.commit()
                return False
            conn.commit()
        return True


# The shared default instance — every runtime consumer goes through this.
store = ServersStore()
