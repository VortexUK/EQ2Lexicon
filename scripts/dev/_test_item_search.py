"""RETIRED — SQLite-era scratch test of the item search query logic.

Exercised the /api/items search SQL (stat joins + sort) directly over the
local items.db via aiosqlite, bypassing FastAPI. Superseded by the Postgres
``items`` schema (db/migrations/0007_items.sql) — the search SQL now lives
in ``backend/server/api/item.py`` against pooled psycopg connections, and
the route tests cover it.

Kept as a stub so the implementation stays reachable in git history.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: SQLite-era scratch test — item search now runs against the Postgres "
        "items schema and is covered by the route tests."
    )
