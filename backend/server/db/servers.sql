-- SQL for backend/server/db/servers.py (per-server registry).

-- :name list_all
SELECT * FROM servers ORDER BY display_name;

-- :name find_by_subdomain
SELECT * FROM servers WHERE subdomain = ?;

-- :name find_by_world
SELECT * FROM servers WHERE world = ?;

-- :name upsert_server_settings
UPDATE servers SET max_level = ?, current_xpac = ?, launch_dt = ?,
       next_xpac = ?, next_xpac_dt = ?,
       updated_at = strftime('%s','now')
WHERE world = ?;

-- :name clear_all_defaults
UPDATE servers SET is_default = 0;

-- :name set_default_by_world
UPDATE servers SET is_default = 1 WHERE world = ?;

-- :name set_default_fallback
UPDATE servers SET is_default = 1 WHERE world =
(SELECT world FROM servers ORDER BY display_name LIMIT 1);

-- The countdown-hit-zero flip (xpac_rollover.py). Guarded on next_xpac
-- still being set: a repeat/concurrent call matches zero rows.
-- :name apply_xpac_rollover
UPDATE servers
   SET current_xpac = ?, max_level = ?, current_xpac_started_dt = ?,
       next_xpac = NULL, next_xpac_dt = NULL,
       updated_at = strftime('%s','now')
 WHERE world = ? AND next_xpac IS NOT NULL;
