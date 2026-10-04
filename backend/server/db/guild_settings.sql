-- SQL for backend/server/db/guild_settings.py (psycopg, users schema).

-- :name select_settings
SELECT officers_can_delete_parses, updated_by, updated_at
FROM guild_settings WHERE world = %s AND guild_name = %s;

-- :name upsert_settings
INSERT INTO guild_settings (world, guild_name, officers_can_delete_parses, updated_by, updated_at)
VALUES (%s, %s, %s, %s, floor(extract(epoch from now())))
ON CONFLICT(world, guild_name) DO UPDATE SET
    officers_can_delete_parses = excluded.officers_can_delete_parses,
    updated_by = excluded.updated_by,
    updated_at = floor(extract(epoch from now()));

-- :name select_delete_flags
-- The name list binds as ONE array parameter (= ANY) — no composed
-- placeholder strings and no variable-count limits.
SELECT guild_name, officers_can_delete_parses
FROM guild_settings WHERE world = %s AND guild_name = ANY(%s);
