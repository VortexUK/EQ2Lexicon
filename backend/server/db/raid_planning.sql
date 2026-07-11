-- SQL for backend/server/db/raid_planning.py (RaidPlanningStore).

-- :name select_roles
SELECT character_name, role, updated_at, updated_by
FROM raid_roster_roles
WHERE world = ? AND guild_name = ?
ORDER BY LOWER(character_name);

-- :name upsert_role
INSERT INTO raid_roster_roles (world, guild_name, character_name, role, updated_at, updated_by)
VALUES (?, ?, ?, ?, strftime('%s','now'), ?)
ON CONFLICT(world, guild_name, character_name) DO UPDATE SET
    role = excluded.role,
    updated_at = excluded.updated_at,
    updated_by = excluded.updated_by;

-- :name delete_role
DELETE FROM raid_roster_roles
WHERE world = ? AND guild_name = ? AND LOWER(character_name) = LOWER(?);

-- :name delete_placements_for_character
DELETE FROM raid_placements
WHERE world = ? AND guild_name = ? AND LOWER(character_name) = LOWER(?);

-- :name select_placements
SELECT character_name, group_num, slot, sitout
FROM raid_placements
WHERE world = ? AND guild_name = ? AND team_index = ?
ORDER BY group_num, slot, LOWER(character_name);

-- :name delete_placements_for_team
DELETE FROM raid_placements
WHERE world = ? AND guild_name = ? AND team_index = ?;

-- :name insert_placement
INSERT INTO raid_placements
    (world, guild_name, team_index, character_name, group_num, slot, sitout, updated_at, updated_by)
VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%s','now'), ?);

-- :name prune_placements_beyond
DELETE FROM raid_placements
WHERE world = ? AND guild_name = ? AND team_index >= ?;

-- Approved claims for a guild's characters: who plays whom. Availability and
-- the duplicate-player warning both key off this. Case-insensitive join side
-- is handled by lower-casing the input names in Python.
-- :name select_claims_for_world
SELECT LOWER(character_name) AS name_lower, discord_id
FROM character_claims
WHERE world = ? AND status = 'approved';

-- Is any of these characters on a raid roster anywhere on this world?
-- Drives the home-page availability panel's is_raider gate. Name matching is
-- case-insensitive via lower-cased input.
-- :name select_roles_for_world
SELECT LOWER(character_name) AS name_lower, role
FROM raid_roster_roles
WHERE world = ?;
