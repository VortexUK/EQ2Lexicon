-- SQL for backend/server/db/site_settings.py (psycopg, users schema).

-- :name select_setting
SELECT value FROM site_settings WHERE key = %s;

-- :name select_all
SELECT key, value FROM site_settings ORDER BY key;

-- :name upsert_setting
INSERT INTO site_settings (key, value, updated_by, updated_at)
VALUES (%s, %s, %s, floor(extract(epoch from now())))
ON CONFLICT(key) DO UPDATE SET
    value = excluded.value,
    updated_by = excluded.updated_by,
    updated_at = floor(extract(epoch from now()));

-- :name delete_setting
DELETE FROM site_settings WHERE key = %s;
