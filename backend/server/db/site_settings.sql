-- SQL for backend/server/db/site_settings.py (async aiosqlite).

-- :name select_setting
SELECT value FROM site_settings WHERE key = ?;

-- :name select_all
SELECT key, value FROM site_settings ORDER BY key;

-- :name upsert_setting
INSERT INTO site_settings (key, value, updated_by, updated_at)
VALUES (?, ?, ?, strftime('%s','now'))
ON CONFLICT(key) DO UPDATE SET
    value = excluded.value,
    updated_by = excluded.updated_by,
    updated_at = strftime('%s','now');

-- :name delete_setting
DELETE FROM site_settings WHERE key = ?;
