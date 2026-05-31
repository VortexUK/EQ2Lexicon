-- ALTER TABLE migrations for users.db.
--
-- Each statement here is idempotent at the SQL level — the Python guards in
-- migrations.py (PRAGMA table_info checks) decide WHETHER to run them based
-- on the column state. CREATE INDEX IF NOT EXISTS / INSERT OR IGNORE blocks
-- are safe to run every boot regardless of guard.

-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------

-- :name alter_users_add_discord_username
ALTER TABLE users ADD COLUMN discord_username TEXT;

-- :name alter_users_add_access_status
ALTER TABLE users ADD COLUMN access_status TEXT NOT NULL DEFAULT 'pending';

-- Existing users were already using the app — approve them all. Only
-- brand-new users (after this migration) start as 'pending'.
-- :name backfill_users_access_status_approved
UPDATE users SET access_status = 'approved';

-- BE-073: notifications endpoint polls list_pending_users every 60s for every
-- logged-in admin — without this index, WHERE access_status = 'pending' is a
-- full table scan. Selectivity is high (most users are approved), so the index
-- pays for itself.
-- :name index_users_access
CREATE INDEX IF NOT EXISTS idx_users_access ON users(access_status);

-- ---------------------------------------------------------------------------
-- character_claims
-- ---------------------------------------------------------------------------

-- SQLite ALTER TABLE does not support non-constant defaults, so we add the
-- column as nullable and backfill existing rows with the current epoch.
-- :name alter_claims_add_requested_at
ALTER TABLE character_claims ADD COLUMN requested_at INTEGER;

-- :name backfill_claims_requested_at
UPDATE character_claims SET requested_at = strftime('%s','now') WHERE requested_at IS NULL;

-- :name alter_claims_add_reviewed_at
ALTER TABLE character_claims ADD COLUMN reviewed_at INTEGER;

-- :name alter_claims_add_reviewed_by
ALTER TABLE character_claims ADD COLUMN reviewed_by TEXT;

-- :name alter_claims_add_note
ALTER TABLE character_claims ADD COLUMN note TEXT;

-- :name alter_claims_add_is_primary
ALTER TABLE character_claims ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0;

-- :name alter_claims_add_world
ALTER TABLE character_claims ADD COLUMN world TEXT NOT NULL DEFAULT 'Varsoon';

-- Index on world is created here (not in schema.sql) so it runs AFTER the
-- ADD COLUMN above. IF NOT EXISTS keeps subsequent boots no-op.
-- :name index_claims_world
CREATE INDEX IF NOT EXISTS idx_claims_world ON character_claims(world);

-- BE-072: supports the "primary claim per (user, world)" filter that hits
-- get_active_claims on every read.
-- :name index_claims_primary
CREATE INDEX IF NOT EXISTS idx_claims_primary ON character_claims(discord_id, world, is_primary);

-- ---------------------------------------------------------------------------
-- item_watch (rebuild to change UNIQUE constraint — SQLite can't ALTER it)
-- ---------------------------------------------------------------------------

-- Multi-statement rebuild; run via conn.executescript. Guarded on the
-- absence of the `world` column so it fires exactly once on a pre-existing
-- DB; no-op on fresh DBs (which get the new shape directly via schema.sql).
-- :name rebuild_item_watch_add_world
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

-- ---------------------------------------------------------------------------
-- role_permissions — seed + drift cleanup
-- ---------------------------------------------------------------------------

-- Drop the officer→edit_content permission seeded by earlier versions.
-- Officer access to raid editing was removed (2026-05-29 decision); only
-- admins + contributors edit content now. No-op once the row is gone.
-- :name drop_officer_edit_content
DELETE FROM role_permissions WHERE role = 'officer' AND capability = 'edit_content';

-- Idempotent seeds — listed here (not schema.sql) so seeds live with the
-- migration sequence that introduced them.
-- :name seed_role_permissions
INSERT OR IGNORE INTO role_permissions (role, capability) VALUES (?, ?);

-- ---------------------------------------------------------------------------
-- servers — registry seed + is_default migration
-- ---------------------------------------------------------------------------

-- :name seed_server
INSERT OR IGNORE INTO servers (world, subdomain, display_name, max_level, current_xpac, launch_dt)
VALUES (?, ?, ?, ?, ?, ?);

-- :name alter_servers_add_is_default
ALTER TABLE servers ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0;

-- :name count_default_servers
SELECT COUNT(*) FROM servers WHERE is_default = 1;

-- :name set_server_default_by_world
UPDATE servers SET is_default = 1 WHERE world = ?;

-- Fall back to the first row alphabetically when EQ2_WORLD isn't a known server.
-- :name set_server_default_fallback
UPDATE servers SET is_default = 1 WHERE world =
(SELECT world FROM servers ORDER BY display_name LIMIT 1);
