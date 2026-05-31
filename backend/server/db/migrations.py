"""ALTER TABLE migrations for users.db.

Each section adds a column or rebuilds a table for a constraint change.
Idempotent — guarded on PRAGMA table_info checks so re-runs are no-ops.

Pattern:
  cols = {row[1] for row in conn.execute("PRAGMA table_info(X)")}
  if "new_col" not in cols:
      conn.execute("ALTER TABLE X ADD COLUMN new_col ...")
      conn.execute("CREATE INDEX IF NOT EXISTS idx_x_new ON X(new_col)")

Memory [[test-migrations-against-old-db-shape]]: every consumer-visible
column-add MUST also be reflected in schema.py for the fresh-DB path. Drift
between the two has bitten the repo before (a column-dependent index in
SCHEMA crashed prod startup against an existing DB).
"""

from __future__ import annotations

import os
import sqlite3


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply every idempotent ALTER + post-ALTER index + seed. Called from init_db."""
    # Migrate: add columns introduced after initial schema
    users_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "discord_username" not in users_cols:
        conn.execute("ALTER TABLE users ADD COLUMN discord_username TEXT")
    if "access_status" not in users_cols:
        conn.execute("ALTER TABLE users ADD COLUMN access_status TEXT NOT NULL DEFAULT 'pending'")
        # Existing users were already using the app — approve them all.
        # Only brand-new users (after this migration) start as 'pending'.
        conn.execute("UPDATE users SET access_status = 'approved'")

    # BE-073: notifications endpoint polls list_pending_users every 60s for
    # every logged-in admin — without this index, the WHERE access_status =
    # 'pending' is a full table scan. Selectivity is high (most users are
    # approved), so the index pays for itself.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_access ON users(access_status)")

    claims_cols = {row[1] for row in conn.execute("PRAGMA table_info(character_claims)")}
    if "requested_at" not in claims_cols:
        # SQLite ALTER TABLE does not support non-constant defaults, so we add the
        # column as nullable and backfill existing rows with the current epoch.
        conn.execute("ALTER TABLE character_claims ADD COLUMN requested_at INTEGER")
        conn.execute("UPDATE character_claims SET requested_at = strftime('%s','now') WHERE requested_at IS NULL")
    if "reviewed_at" not in claims_cols:
        conn.execute("ALTER TABLE character_claims ADD COLUMN reviewed_at INTEGER")
    if "reviewed_by" not in claims_cols:
        conn.execute("ALTER TABLE character_claims ADD COLUMN reviewed_by TEXT")
    if "note" not in claims_cols:
        conn.execute("ALTER TABLE character_claims ADD COLUMN note TEXT")
    if "is_primary" not in claims_cols:
        conn.execute("ALTER TABLE character_claims ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0")
    if "world" not in claims_cols:
        conn.execute("ALTER TABLE character_claims ADD COLUMN world TEXT NOT NULL DEFAULT 'Varsoon'")
    # Index on world is created here (not in schema.py) so it runs AFTER the
    # ADD COLUMN above — the column is now guaranteed to exist on both fresh
    # and migrated DBs. IF NOT EXISTS makes it a no-op on subsequent boots.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_world ON character_claims(world)")

    # BE-072: idx_claims_primary supports the "primary claim per (user, world)"
    # filter that hits get_active_claims on every read. Without it, the WHERE
    # is_primary = 1 was a full scan across the user's claims (small per-user
    # but multiplied across every read).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_primary ON character_claims(discord_id, world, is_primary)")

    # Migrate item_watch: add world column + rebuild to change UNIQUE constraint.
    # SQLite cannot ALTER a table-level UNIQUE, so we do a table rename → create
    # new → INSERT … SELECT → drop old.  Guarded on the column so it runs exactly
    # once and is a no-op on fresh DBs (which get the new schema directly).
    watch_cols = {row[1] for row in conn.execute("PRAGMA table_info(item_watch)")}
    if "world" not in watch_cols:
        conn.executescript(
            """
            ALTER TABLE item_watch RENAME TO item_watch_old;
            CREATE TABLE item_watch (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                world           TEXT    NOT NULL DEFAULT 'Varsoon',
                guild_name      TEXT    NOT NULL,
                character_name  TEXT    NOT NULL,
                item_id         INTEGER NOT NULL,
                item_name       TEXT    NOT NULL,
                added_by        TEXT    NOT NULL REFERENCES users(discord_id),
                added_by_name   TEXT    NOT NULL,
                added_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                first_seen_at   INTEGER,
                last_seen_at    INTEGER,
                last_checked_at INTEGER,
                UNIQUE(world, guild_name, character_name, item_id)
            );
            INSERT INTO item_watch (id, world, guild_name, character_name, item_id, item_name,
                                    added_by, added_by_name, added_at, first_seen_at, last_seen_at, last_checked_at)
                SELECT id, 'Varsoon', guild_name, character_name, item_id, item_name,
                       added_by, added_by_name, added_at, first_seen_at, last_seen_at, last_checked_at
                FROM item_watch_old;
            DROP TABLE item_watch_old;
            CREATE INDEX IF NOT EXISTS idx_watch_guild ON item_watch(guild_name);
            """
        )

    # Seed role_permissions. INSERT OR IGNORE keeps it idempotent and
    # leaves any admin-edited rows alone if/when a future UI exposes the
    # table for live edits. Listed here (not in schema.py) so the seed
    # statements live with their semantic intent.

    # Drop the officer→edit_content permission that was seeded in earlier
    # versions. Officer access to raid editing is being removed per the
    # 2026-05-29 decision; only admins + contributors edit content now.
    # Idempotent: no-op once the row is gone.
    conn.execute("DELETE FROM role_permissions WHERE role = 'officer' AND capability = 'edit_content'")

    conn.executemany(
        "INSERT OR IGNORE INTO role_permissions (role, capability) VALUES (?, ?)",
        [
            ("contributor", "edit_content"),
        ],
    )

    # Seed the servers registry (idempotent). Varsoon takes the current env
    # values; Wuoshi starts with defaults edited later in admin.
    from backend.census import config as _cfg

    conn.execute(
        "INSERT OR IGNORE INTO servers (world, subdomain, display_name, max_level, current_xpac, launch_dt) "
        "VALUES (?, ?, ?, ?, ?, ?)",
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
        "INSERT OR IGNORE INTO servers (world, subdomain, display_name, max_level, current_xpac, launch_dt) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("Wuoshi", "wuoshi", "Wuoshi", _cfg.SERVER_MAX_LEVEL, os.getenv("SERVER_CURRENT_XPAC") or None, None),
    )

    # Migrate: add is_default column (absent from the pre-existing prod servers table).
    # MUST run after the seed INSERTs above so it is safe to UPDATE on first boot.
    # Nothing is_default-dependent may live in schema.py — see note there.
    servers_cols = {row[1] for row in conn.execute("PRAGMA table_info(servers)")}
    if "is_default" not in servers_cols:
        conn.execute("ALTER TABLE servers ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0")
    # Ensure exactly one default exists. On a fresh DB or after ADD COLUMN every row
    # has is_default=0 until we set one. Default to the EQ2_WORLD server.
    if conn.execute("SELECT COUNT(*) FROM servers WHERE is_default = 1").fetchone()[0] == 0:
        conn.execute("UPDATE servers SET is_default = 1 WHERE world = ?", (_cfg.WORLD,))
        # If EQ2_WORLD isn't a known server, fall back to the first row alphabetically.
        if conn.execute("SELECT COUNT(*) FROM servers WHERE is_default = 1").fetchone()[0] == 0:
            conn.execute(
                "UPDATE servers SET is_default = 1 WHERE world = "
                "(SELECT world FROM servers ORDER BY display_name LIMIT 1)"
            )

    conn.commit()
