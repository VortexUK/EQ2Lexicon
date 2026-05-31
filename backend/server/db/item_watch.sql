-- SQL for backend/server/db/item_watch.py (async aiosqlite).

-- :name add_watch
INSERT INTO item_watch
    (world, guild_name, character_name, item_id, item_name, added_by, added_by_name)
VALUES (?, ?, ?, ?, ?, ?, ?);

-- :name find_by_id
SELECT * FROM item_watch WHERE id = ?;

-- :name list_for_guild
SELECT * FROM item_watch WHERE guild_name = ? AND world = ? ORDER BY added_at DESC;

-- :name remove_watch
DELETE FROM item_watch WHERE id = ? AND guild_name = ? AND world = ?;

-- :name update_seen
UPDATE item_watch SET
    last_checked_at = strftime('%s','now'),
    last_seen_at    = strftime('%s','now'),
    first_seen_at   = COALESCE(first_seen_at, strftime('%s','now'))
WHERE id = ?;

-- :name update_unseen
UPDATE item_watch SET last_checked_at = strftime('%s','now') WHERE id = ?;
