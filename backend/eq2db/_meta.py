"""Shared ``_meta`` key/value table — schema + helpers.

Every reference DB built/maintained under ``backend/eq2db/`` (items, raids,
recipes, spells, zones) carries the same ``_meta`` table with the same
schema and the same get/set helpers. This module is the single source of
truth: the per-DB modules re-export ``get_meta`` / ``set_meta`` and call
``create_table(conn)`` from their own ``init_db()``.
"""

from __future__ import annotations

import sqlite3

from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


def create_table(conn: sqlite3.Connection) -> None:
    """Idempotent CREATE TABLE for the shared ``_meta`` table. Called from
    each eq2db module's ``init_db()`` before any get/set use."""
    conn.execute(_SQL["create_table"])


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    """Return the value for ``key`` or ``default`` if missing."""
    row = conn.execute(_SQL["select_value"], (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert ``(key, value)``. Commits immediately — meta writes are
    one-shot and don't compose into larger transactions."""
    conn.execute(_SQL["upsert"], (key, value))
    conn.commit()
