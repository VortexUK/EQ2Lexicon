-- SQL for backend/server/db/guild_settings.py (async aiosqlite).

-- :name select_settings
SELECT officers_can_delete_parses, updated_by, updated_at
FROM guild_settings WHERE world = ? AND guild_name = ?;

-- :name upsert_settings
INSERT INTO guild_settings (world, guild_name, officers_can_delete_parses, updated_by, updated_at)
VALUES (?, ?, ?, ?, strftime('%s','now'))
ON CONFLICT(world, guild_name) DO UPDATE SET
    officers_can_delete_parses = excluded.officers_can_delete_parses,
    updated_by = excluded.updated_by,
    updated_at = strftime('%s','now');

-- :name select_delete_flags
-- {placeholders} = "?,?,…" composed in Python for the guild-name IN list.
SELECT guild_name, officers_can_delete_parses
FROM guild_settings WHERE world = ? AND guild_name IN ({placeholders});
