"""ALTER TABLE migrations for users.db.

Each section adds a column or rebuilds a table for a constraint change.
Idempotent — guarded on PRAGMA table_info checks so re-runs are no-ops.

Pattern:
  cols = {row[1] for row in conn.execute("PRAGMA table_info(X)")}
  if "new_col" not in cols:
      conn.execute(_SQL["alter_X_add_new_col"])
      conn.execute(_SQL["index_X_new"])

SQL statements live in migrations.sql alongside. The Python guards stay
here because they're not SQL — they're "WHEN to apply the ALTER" logic.

Memory [[test-migrations-against-old-db-shape]]: every consumer-visible
column-add MUST also be reflected in schema.sql for the fresh-DB path.
Drift between the two has bitten the repo before (a column-dependent
index in SCHEMA crashed prod startup against an existing DB).
"""

from __future__ import annotations

import os
import sqlite3

from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply every idempotent ALTER + post-ALTER index + seed. Called from init_db."""
    # Migrate: add columns introduced after initial schema
    users_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "discord_username" not in users_cols:
        conn.execute(_SQL["alter_users_add_discord_username"])
    if "access_status" not in users_cols:
        conn.execute(_SQL["alter_users_add_access_status"])
        conn.execute(_SQL["backfill_users_access_status_approved"])

    conn.execute(_SQL["index_users_access"])

    claims_cols = {row[1] for row in conn.execute("PRAGMA table_info(character_claims)")}
    if "requested_at" not in claims_cols:
        conn.execute(_SQL["alter_claims_add_requested_at"])
        conn.execute(_SQL["backfill_claims_requested_at"])
    if "reviewed_at" not in claims_cols:
        conn.execute(_SQL["alter_claims_add_reviewed_at"])
    if "reviewed_by" not in claims_cols:
        conn.execute(_SQL["alter_claims_add_reviewed_by"])
    if "note" not in claims_cols:
        conn.execute(_SQL["alter_claims_add_note"])
    if "is_primary" not in claims_cols:
        conn.execute(_SQL["alter_claims_add_is_primary"])
    if "world" not in claims_cols:
        conn.execute(_SQL["alter_claims_add_world"])
    conn.execute(_SQL["index_claims_world"])
    conn.execute(_SQL["index_claims_primary"])

    # Migrate item_watch: add world column + rebuild to change UNIQUE constraint.
    watch_cols = {row[1] for row in conn.execute("PRAGMA table_info(item_watch)")}
    if "world" not in watch_cols:
        conn.executescript(_SQL["rebuild_item_watch_add_world"])

    # Drop the officer→edit_content permission seeded by earlier versions.
    conn.execute(_SQL["drop_officer_edit_content"])

    conn.executemany(
        _SQL["seed_role_permissions"],
        [
            ("contributor", "edit_content"),
        ],
    )

    # Seed the servers registry (idempotent). Varsoon takes the current env
    # values; Wuoshi starts with defaults edited later in admin.
    from backend.census import config as _cfg

    conn.execute(
        _SQL["seed_server"],
        (
            "Varsoon",
            "varsoon",
            "Varsoon",
            _cfg.SERVER_MAX_LEVEL,
            os.getenv("SERVER_CURRENT_XPAC") or None,
            _cfg.LAUNCH_DT_ISO or None,
        ),
    )
    conn.execute(
        _SQL["seed_server"],
        ("Wuoshi", "wuoshi", "Wuoshi", _cfg.SERVER_MAX_LEVEL, os.getenv("SERVER_CURRENT_XPAC") or None, None),
    )

    # Migrate: add is_default column (absent from the pre-existing prod servers
    # table). MUST run after the seed INSERTs above so it is safe to UPDATE on
    # first boot. Nothing is_default-dependent may live in schema.sql.
    servers_cols = {row[1] for row in conn.execute("PRAGMA table_info(servers)")}
    if "is_default" not in servers_cols:
        conn.execute(_SQL["alter_servers_add_is_default"])
    # Ensure exactly one default exists. On a fresh DB or after ADD COLUMN every
    # row has is_default=0 until we set one. Default to the EQ2_WORLD server.
    if conn.execute(_SQL["count_default_servers"]).fetchone()[0] == 0:
        conn.execute(_SQL["set_server_default_by_world"], (_cfg.WORLD,))
        # If EQ2_WORLD isn't a known server, fall back to the first row alphabetically.
        if conn.execute(_SQL["count_default_servers"]).fetchone()[0] == 0:
            conn.execute(_SQL["set_server_default_fallback"])

    conn.commit()
