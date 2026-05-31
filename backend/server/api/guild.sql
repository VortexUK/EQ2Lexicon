-- SQL for backend/server/api/guild.py — guild name search fallback.

-- :name local_guild_search_by_prefix
-- Locally-tracked guilds whose name starts with the lowercased query.
-- Returns distinct names ordered alphabetically. Used when Census is
-- unavailable.
SELECT DISTINCT guild_name
FROM item_watch
WHERE LOWER(guild_name) LIKE ?
ORDER BY guild_name
LIMIT 25;
