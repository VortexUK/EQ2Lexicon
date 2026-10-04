-- SQL for backend/server/db/item_watch.py (psycopg, users schema).

-- :name add_watch
INSERT INTO item_watch
    (world, guild_name, character_name, item_id, item_name, added_by, added_by_name)
VALUES (%s, %s, %s, %s, %s, %s, %s)
RETURNING id;

-- :name find_by_id
SELECT * FROM item_watch WHERE id = %s;

-- :name list_for_guild
SELECT * FROM item_watch WHERE guild_name = %s AND world = %s ORDER BY added_at DESC;

-- :name remove_watch
DELETE FROM item_watch WHERE id = %s AND guild_name = %s AND world = %s;

-- :name update_seen
UPDATE item_watch SET
    last_checked_at = floor(extract(epoch from now())),
    last_seen_at    = floor(extract(epoch from now())),
    first_seen_at   = COALESCE(first_seen_at, floor(extract(epoch from now())))
WHERE id = %s;

-- :name update_unseen
UPDATE item_watch SET last_checked_at = floor(extract(epoch from now())) WHERE id = %s;
