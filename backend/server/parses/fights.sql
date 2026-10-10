-- SQL sidecar for backend/server/parses/fights.py (parses schema).
-- ---------------------------------------------------------------------------

-- :name select_encounter_for_attach
-- The upload being grouped, with the live player count the merge gate sizes
-- its top-N by (N = 3 when either side is raid-sized).
SELECT e.id, e.world, e.title, e.guild_name, e.uploaded_by, e.started_at, e.duration_s,
       e.hidden_at, e.uploader_verified, e.success_level, e.fight_id,
       (SELECT COUNT(*) FROM combatants c WHERE c.encounter_id = e.id AND c.is_player = 1) AS player_count
FROM encounters e
WHERE e.id = %s;

-- :name select_candidate_fights
-- Fights of the same world/boss/guild with a member started within the
-- mirror window of the new upload (the window bounds are passed in).
SELECT id, world, title_key, guild_name, first_started_at, last_started_at,
       primary_encounter_id, primary_winning_encounter_id, upload_count
FROM fights
WHERE world = %s AND title_key = %s AND guild_name = %s
  AND last_started_at >= %s AND first_started_at <= %s
ORDER BY first_started_at ASC, id ASC;

-- :name select_fight
SELECT id, world, title_key, guild_name, first_started_at, last_started_at,
       primary_encounter_id, primary_winning_encounter_id, upload_count
FROM fights
WHERE id = %s;

-- :name select_fight_members
SELECT e.id, e.uploaded_by, e.started_at, e.duration_s, e.hidden_at, e.uploader_verified, e.success_level,
       e.ranking_barred_at,
       (SELECT COUNT(*) FROM combatants c WHERE c.encounter_id = e.id AND c.is_player = 1) AS player_count
FROM encounters e
WHERE e.fight_id = %s
ORDER BY e.started_at ASC, e.id ASC;

-- :name player_rosters_bulk
-- Ordered player rosters for the mutual-containment gate: top-N is the
-- first N names (encDPS DESC, name ASC tiebreak so the N is deterministic).
SELECT encounter_id, name FROM combatants
WHERE encounter_id = ANY(%s) AND is_player = 1
ORDER BY encounter_id, encdps DESC, name ASC;

-- :name insert_fight
INSERT INTO fights (world, title_key, guild_name, first_started_at, last_started_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s)
RETURNING id;

-- :name set_encounter_fight
UPDATE encounters SET fight_id = %s WHERE id = %s;

-- :name set_fight_for_encounters
UPDATE encounters SET fight_id = %s WHERE id = ANY(%s);

-- :name select_encounter_fight_id
SELECT fight_id FROM encounters WHERE id = %s;

-- :name update_fight
UPDATE fights
SET first_started_at = %s, last_started_at = %s, primary_encounter_id = %s,
    primary_winning_encounter_id = %s, upload_count = %s, updated_at = %s
WHERE id = %s;

-- :name delete_fight
DELETE FROM fights WHERE id = %s;

-- :name count_ungrouped
SELECT COUNT(*) AS n FROM encounters WHERE world = %s AND fight_id IS NULL;

-- :name select_ungrouped_ids
SELECT id FROM encounters
WHERE world = %s AND fight_id IS NULL
ORDER BY started_at ASC, id ASC
LIMIT %s;

-- :name select_ungrouped_for_bulk
-- Everything the full grouper needs, in the order it attaches.
SELECT e.id, e.title, e.guild_name, e.uploaded_by, e.started_at, e.duration_s, e.zone, e.hidden_at,
       (SELECT COUNT(*) FROM combatants c WHERE c.encounter_id = e.id AND c.is_player = 1) AS player_count
FROM encounters e
WHERE e.world = %s AND e.fight_id IS NULL
ORDER BY e.started_at ASC, e.id ASC;

-- :name select_worlds_with_ungrouped
SELECT DISTINCT world FROM encounters WHERE fight_id IS NULL;

-- :name select_aged_fights
-- Retention sweep: fights whose newest member is older than the cutoff.
SELECT id, primary_encounter_id, primary_winning_encounter_id
FROM fights
WHERE world = %s AND last_started_at < %s
ORDER BY id ASC;
