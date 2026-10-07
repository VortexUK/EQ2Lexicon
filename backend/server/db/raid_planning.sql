-- SQL for backend/server/db/raid_planning.py (psycopg, users schema).

-- :name select_roles
SELECT character_name, role, placeholder, cls, updated_at, updated_by
FROM raid_roster_roles
WHERE world = %s AND guild_name = %s
ORDER BY LOWER(character_name);

-- A normal (placeholder=0) write for the same name supersedes an earlier
-- placeholder row wholesale — the census-visible character replaces the
-- hand-added stand-in.
-- :name upsert_role
INSERT INTO raid_roster_roles (world, guild_name, character_name, role, placeholder, cls, updated_at, updated_by)
VALUES (%s, %s, %s, %s, %s, %s, floor(extract(epoch from now())), %s)
ON CONFLICT(world, guild_name, character_name) DO UPDATE SET
    role = excluded.role,
    placeholder = excluded.placeholder,
    cls = excluded.cls,
    updated_at = excluded.updated_at,
    updated_by = excluded.updated_by;

-- :name delete_role
DELETE FROM raid_roster_roles
WHERE world = %s AND guild_name = %s AND LOWER(character_name) = LOWER(%s);

-- :name delete_placements_for_character
DELETE FROM raid_placements
WHERE world = %s AND guild_name = %s AND LOWER(character_name) = LOWER(%s);

-- :name select_placements
-- NULLS FIRST: benched rows (NULL group_num/slot) sort before placed ones;
-- Postgres defaults to NULLS LAST.
SELECT character_name, group_num, slot, sitout
FROM raid_placements
WHERE world = %s AND guild_name = %s AND team_index = %s
ORDER BY group_num NULLS FIRST, slot NULLS FIRST, LOWER(character_name);

-- :name delete_placements_for_team
DELETE FROM raid_placements
WHERE world = %s AND guild_name = %s AND team_index = %s;

-- :name insert_placement
INSERT INTO raid_placements
    (world, guild_name, team_index, character_name, group_num, slot, sitout, updated_at, updated_by)
VALUES (%s, %s, %s, %s, %s, %s, %s, floor(extract(epoch from now())), %s);

-- :name prune_placements_beyond
DELETE FROM raid_placements
WHERE world = %s AND guild_name = %s AND team_index >= %s;

-- Approved claims for a guild's characters: who plays whom. Availability and
-- the duplicate-player warning both key off this. Case-insensitive join side
-- is handled by lower-casing the input names in Python.
-- :name select_claims_for_world
SELECT LOWER(character_name) AS name_lower, discord_id
FROM character_claims
WHERE world = %s AND status = 'approved';

-- Primary-claim flags for main resolution (attendance "raid main"
-- attribution): which of a player's claims is their designated primary.
-- :name select_primary_claims_for_world
SELECT LOWER(character_name) AS name_lower
FROM character_claims
WHERE world = %s AND status = 'approved' AND is_primary = 1;

-- Is any of these characters on a raid roster anywhere on this world?
-- Drives the home-page availability panel's is_raider gate. Name matching is
-- case-insensitive via lower-cased input.
-- :name select_roles_for_world
SELECT LOWER(character_name) AS name_lower, role
FROM raid_roster_roles
WHERE world = %s;
