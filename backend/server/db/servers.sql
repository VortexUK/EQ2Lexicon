-- SQL for backend/server/db/servers.py (per-server registry).

-- :name list_all
SELECT * FROM servers ORDER BY display_name;

-- :name find_by_subdomain
SELECT * FROM servers WHERE subdomain = %s;

-- :name find_by_world
SELECT * FROM servers WHERE world = %s;

-- :name upsert_server_settings
UPDATE servers SET max_level = %s, current_xpac = %s, launch_dt = %s,
       next_xpac = %s, next_xpac_dt = %s,
       updated_at = floor(extract(epoch from now()))
WHERE world = %s;

-- :name clear_all_defaults
UPDATE servers SET is_default = 0;

-- :name set_default_by_world
UPDATE servers SET is_default = 1 WHERE world = %s;

-- :name set_default_fallback
UPDATE servers SET is_default = 1 WHERE world =
(SELECT world FROM servers ORDER BY display_name LIMIT 1);

-- The countdown-hit-zero flip (xpac_rollover.py). Guarded on next_xpac
-- still being set: a repeat/concurrent call matches zero rows.
-- :name apply_xpac_rollover
UPDATE servers
   SET current_xpac = %s, max_level = %s, current_xpac_started_dt = %s,
       next_xpac = NULL, next_xpac_dt = NULL,
       updated_at = floor(extract(epoch from now()))
 WHERE world = %s AND next_xpac IS NOT NULL;
