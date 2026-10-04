-- SQL for backend/server/db/aa_plans.py (psycopg, users schema). Schema DDL
-- lives in db/migrations/0001_users.sql.

-- :name select_plans_for_character
SELECT id, name, xpac, share_slug, created_at, updated_at
  FROM aa_plans
 WHERE discord_id = %s AND world = %s AND character_name = %s
 ORDER BY updated_at DESC;

-- :name count_plans_for_character
SELECT COUNT(*) AS n FROM aa_plans WHERE discord_id = %s AND world = %s AND character_name = %s;

-- :name select_plan
SELECT id, discord_id, world, character_name, name, xpac, allocations, share_slug, created_at, updated_at
  FROM aa_plans
 WHERE id = %s;

-- :name select_plan_by_slug
SELECT id, discord_id, world, character_name, name, xpac, allocations, share_slug, created_at, updated_at
  FROM aa_plans
 WHERE share_slug = %s;

-- :name insert_plan
INSERT INTO aa_plans (discord_id, world, character_name, name, xpac, allocations, share_slug)
VALUES (%s, %s, %s, %s, %s, %s, %s)
RETURNING id;

-- :name update_plan
UPDATE aa_plans
   SET name = %s, allocations = %s, xpac = %s, updated_at = floor(extract(epoch from now()))
 WHERE id = %s AND discord_id = %s;

-- :name delete_plan
DELETE FROM aa_plans WHERE id = %s AND discord_id = %s;
