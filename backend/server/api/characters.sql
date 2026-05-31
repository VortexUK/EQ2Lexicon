-- SQL for backend/server/api/characters.py — character name search.

-- :name local_search_by_prefix
-- Claimed characters whose name starts with the lowercased query. Returns
-- distinct names ordered alphabetically. Fallback when Census is unavailable.
SELECT DISTINCT character_name
FROM character_claims
WHERE LOWER(character_name) LIKE ?
  AND status IN ('approved', 'pending')
ORDER BY character_name
LIMIT 50;
