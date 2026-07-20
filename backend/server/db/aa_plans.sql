-- users.db aa_plans DML (schema lives in schema.sql — the package init_db
-- orchestrator owns it).

-- :name select_plans_for_character
SELECT id, name, xpac, share_slug, created_at, updated_at
  FROM aa_plans
 WHERE discord_id = ? AND world = ? AND character_name = ?
 ORDER BY updated_at DESC;

-- :name count_plans_for_character
SELECT COUNT(*) FROM aa_plans WHERE discord_id = ? AND world = ? AND character_name = ?;

-- :name select_plan
SELECT id, discord_id, world, character_name, name, xpac, allocations, share_slug, created_at, updated_at
  FROM aa_plans
 WHERE id = ?;

-- :name select_plan_by_slug
SELECT id, discord_id, world, character_name, name, xpac, allocations, share_slug, created_at, updated_at
  FROM aa_plans
 WHERE share_slug = ?;

-- :name insert_plan
INSERT INTO aa_plans (discord_id, world, character_name, name, xpac, allocations, share_slug)
VALUES (?, ?, ?, ?, ?, ?, ?);

-- :name update_plan
UPDATE aa_plans
   SET name = ?, allocations = ?, xpac = ?, updated_at = strftime('%s','now')
 WHERE id = ? AND discord_id = ?;

-- :name delete_plan
DELETE FROM aa_plans WHERE id = ? AND discord_id = ?;
