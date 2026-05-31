"""Shared ``_meta`` key/value table helpers.

Every reference DB built/maintained under ``backend/eq2db/`` (items, raids,
recipes, spells, zones) carries the same ``_meta`` table with the same
schema and the same get/set helpers. This module is the single source of
truth — the per-DB modules re-export these so callers keep using
``items.get_meta(conn, "last_synced")`` etc. without a churn.

The table itself is created in each DB's ``init_db()`` since the schema
sits inside the migration block where it's used. Only the read/write
queries live in :file:`_meta.sql`.
"""

from __future__ import annotations

import sqlite3

from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    """Return the value for ``key`` or ``default`` if missing."""
    row = conn.execute(_SQL["select_value"], (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert ``(key, value)``. Commits immediately — meta writes are
    one-shot and don't compose into larger transactions."""
    conn.execute(_SQL["upsert"], (key, value))
    conn.commit()
