-- SQL for backend/server/db/favorites.py (async aiosqlite).

-- Cap-guarded insert: atomic within SQLite, so concurrent PUTs cannot race the
-- check-then-insert past the per-world cap. Rowcount 0 means EITHER the row
-- already existed (OR IGNORE) or the cap was hit — callers disambiguate with
-- select_is_favorited.
-- :name insert_favorite_capped
INSERT OR IGNORE INTO character_favorites (discord_id, character_name, world)
SELECT ?, ?, ?
WHERE (SELECT COUNT(*) FROM character_favorites WHERE discord_id = ? AND world = ?) < ?;

-- :name delete_favorite
DELETE FROM character_favorites WHERE discord_id = ? AND character_name = ? AND world = ?;

-- :name select_is_favorited
SELECT 1 FROM character_favorites WHERE discord_id = ? AND character_name = ? AND world = ?;

-- :name count_for_character
SELECT COUNT(*) FROM character_favorites WHERE character_name = ? AND world = ?;

-- :name count_for_user
SELECT COUNT(*) FROM character_favorites WHERE discord_id = ? AND world = ?;

-- :name select_for_user
SELECT character_name, world, created_at FROM character_favorites
WHERE discord_id = ? AND world = ? ORDER BY created_at DESC, id DESC;
