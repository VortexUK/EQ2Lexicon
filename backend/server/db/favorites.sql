-- SQL for backend/server/db/favorites.py (psycopg, users schema).

-- Cap-guarded insert: the count check rides in the INSERT itself (no route-
-- layer check-then-insert window). NOTE: under Postgres read-committed two
-- concurrent inserts can each see count < cap and land cap+1 —
-- acceptable drift for a soft bookmark cap. Rowcount 0 means EITHER the row already existed (ON CONFLICT DO
-- NOTHING) or the cap was hit — callers disambiguate with select_is_favorited.
-- :name insert_favorite_capped
INSERT INTO character_favorites (discord_id, character_name, world)
SELECT %s, %s, %s
WHERE (SELECT COUNT(*) FROM character_favorites WHERE discord_id = %s AND world = %s) < %s
ON CONFLICT DO NOTHING;

-- :name delete_favorite
DELETE FROM character_favorites WHERE discord_id = %s AND character_name = %s AND world = %s;

-- :name select_is_favorited
SELECT 1 FROM character_favorites WHERE discord_id = %s AND character_name = %s AND world = %s;

-- :name count_for_character
SELECT COUNT(*) AS n FROM character_favorites WHERE character_name = %s AND world = %s;

-- :name count_for_user
SELECT COUNT(*) AS n FROM character_favorites WHERE discord_id = %s AND world = %s;

-- :name select_for_user
SELECT character_name, world, created_at FROM character_favorites
WHERE discord_id = %s AND world = %s ORDER BY created_at DESC, id DESC;
