-- Undo of db/migrations/0028_parses_located_deaths.sql.
--
-- Puts back, from the three located_death_* tables that migration filled:
-- the located combatant rows (whole, with their original ids), the death
-- counts on the rows they were folded into, encounters.success_level and
-- encounters.deaths, and the ranking primary of the fights whose outcome
-- changed. Then it empties the three tables, so the data is exactly as the
-- migration found it and this script cannot be applied twice.
--
-- One transaction: any error rolls all of it back. Run it with psql and
-- ON_ERROR_STOP set, against the database the migration ran on.
--
-- What it cannot bring back:
--   * an encounter deleted since the repair (retention, an admin purge).
--     Its rows are skipped;
--   * uploads the retention sweep removed BECAUSE of the repair. A fight
--     keeps its longest winning upload; where a repaired upload became that,
--     the sweep deletes the shorter win that used to rank, once the fight is
--     older than PARSE_RETENTION_DAYS. The sweep runs shortly after every
--     startup and then every 6 hours, so for fights of that age this
--     happens right after the deploy that ran the repair. After an undo
--     such a fight has no winning upload left and stops ranking. The dry
--     run's fights query lists them (ranking_primary_now is set and differs
--     from ranking_primary_after).
--
-- The site caches rankings and the parses list in process: after an undo
-- they catch up on the next hourly rebuild, or at once on a restart.
--
-- To repair again after an undo, run the migration file by hand: it is
-- idempotent and the ledger row does not stop a manual run.

BEGIN;
set search_path to parses, public;

-- Same lock order as the migration: encounters first.
SELECT count(*) AS encounters_locked
FROM (
    SELECT e.id FROM encounters e
    WHERE e.id IN (SELECT encounter_id FROM located_death_encounters)
    ORDER BY e.id
    FOR NO KEY UPDATE OF e
) locked;

-- 1. A renamed row takes back its located name, its own death count (the
-- merged rows return below with theirs) and its classification.
UPDATE combatants c
SET name = l.located_name,
    deaths = l.deaths,
    is_player = (l.row_json::jsonb ->> 'is_player')::integer
FROM located_death_rows l
WHERE l.action = 'renamed' AND c.id = l.combatant_id;

-- 2. A real-name row gives back the deaths folded into it.
UPDATE combatants c
SET deaths = c.deaths - s.added
FROM (
    SELECT target_combatant_id, sum(deaths)::bigint AS added
    FROM located_death_rows
    WHERE action = 'folded'
    GROUP BY target_combatant_id
) s
WHERE c.id = s.target_combatant_id;

-- 3. The deleted located rows return whole, ids included.
INSERT INTO combatants OVERRIDING SYSTEM VALUE
SELECT (jsonb_populate_record(NULL::combatants, l.row_json::jsonb)).*
FROM located_death_rows l
WHERE l.action IN ('folded', 'merged')
  AND EXISTS (SELECT 1 FROM encounters e WHERE e.id = l.encounter_id);

-- 4. So do any breakdown rows that hung off them.
INSERT INTO attack_types OVERRIDING SYSTEM VALUE
SELECT (jsonb_populate_record(NULL::attack_types, k.row_json::jsonb)).*
FROM located_death_children k
WHERE k.source_table = 'attack_types'
  AND EXISTS (SELECT 1 FROM combatants c WHERE c.id = k.combatant_id);

INSERT INTO damage_types OVERRIDING SYSTEM VALUE
SELECT (jsonb_populate_record(NULL::damage_types, k.row_json::jsonb)).*
FROM located_death_children k
WHERE k.source_table = 'damage_types'
  AND EXISTS (SELECT 1 FROM combatants c WHERE c.id = k.combatant_id);

-- 5. Outcome and the encounter's death total.
UPDATE encounters e
SET success_level = r.old_success_level, deaths = r.old_deaths
FROM located_death_encounters r
WHERE r.encounter_id = e.id;

-- 6. Ranking primary of the fights whose outcome just went back. Same rule
-- as the migration and parses/fights.py _primaries.
UPDATE fights f
SET primary_winning_encounter_id = (
        SELECT e.id FROM encounters e
        WHERE e.fight_id = f.id AND e.hidden_at IS NULL AND e.uploader_verified = 1
          AND e.success_level = 1 AND e.ranking_barred_at IS NULL
        ORDER BY e.duration_s DESC, e.started_at ASC, e.id ASC LIMIT 1),
    updated_at = floor(extract(epoch from now()))
WHERE f.id IN (
    SELECT e.fight_id
    FROM encounters e
    JOIN located_death_encounters r ON r.encounter_id = e.id
    WHERE e.fight_id IS NOT NULL AND r.new_success_level <> r.old_success_level);

-- 7. The records are spent.
DELETE FROM located_death_children;
DELETE FROM located_death_rows;
DELETE FROM located_death_encounters;

COMMIT;
