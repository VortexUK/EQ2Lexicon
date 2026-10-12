create schema if not exists parses;
set search_path to parses, public;

-- Data repair: deaths the EQ2Parser client filed under a mangled name.
--
-- Some game clients log a death with where it happened after the victim's
-- name. EQ2Parser (fixed client-side) took the whole tail as the name, so an
-- upload carries, next to the real combatant, rows named
--     <real name> in <zone> (<x>, <y>, <z>)
-- holding the deaths (no damage, duration 0), and the real row is short of
-- them. A boss that died this way shows 0 deaths, so the client's outcome
-- rule called a clean kill a partial (success_level 2). The site ranks,
-- retains and counts kill progress on success_level = 1 only.
--
-- What this file does, in one transaction:
--   1. records what it is about to change (the three tables below);
--   2. adds each located row's deaths to the row of the same encounter named
--      like its real name, and deletes the located row. When no such row
--      exists the first located row is renamed to the real name and the
--      others are merged into it;
--   3. adds the same deaths to encounters.deaths where the client would have
--      counted them (an allied, player-shaped name);
--   4. raises success_level 2 to 1 where the client's rule now says win, and
--      never touches any other value;
--   5. recomputes primary_winning_encounter_id for the fights it changed.
--
-- The real name is everything before the first ' in ' (the client's own fix
-- uses the same cut). A row is only treated as located when the text
-- between that ' in ' and the coordinates is the encounter's zone, or the
-- encounter has no zone: anything else is left alone.
--
-- Undo: scripts/dev/undo_located_deaths.sql puts every row back from the
-- three tables. They carry no foreign key on purpose: adding one would lock
-- encounters and combatants against writers for the whole transaction, and
-- an undo record that outlives its encounter is harmless.
--
-- Locks: ordinary row locks on about 6,000 combatants and 750 encounters,
-- taken encounter first (the order the store's own writers use). No table
-- lock on an existing table.
--
-- Idempotent: an encounter is worked on only while its record is open
-- (new_success_level IS NULL), and step 6 closes every record. A second run
-- finds no located row and no open record.

-- One row per encounter that had a located row.
CREATE TABLE IF NOT EXISTS located_death_encounters (
    encounter_id       bigint  PRIMARY KEY,
    world              text    NOT NULL,
    old_success_level  integer NOT NULL,
    new_success_level  integer,           -- NULL only while the repair runs
    old_deaths         bigint  NOT NULL,  -- encounters.deaths before
    new_deaths         bigint,
    repaired_at        bigint  NOT NULL
);
ALTER TABLE located_death_encounters ENABLE ROW LEVEL SECURITY;

-- One row per located combatant row.
--   action = folded   deaths added to target_combatant_id (the real-name
--                     row), this row deleted
--          = renamed  no real-name row: this row kept under real_name
--                     (target_combatant_id is itself)
--          = merged   no real-name row: deaths added to the renamed row
--                     (target_combatant_id), this row deleted
--   row_json  the whole original combatants row, id included (text, like
--             every JSON-ish column of this family).
CREATE TABLE IF NOT EXISTS located_death_rows (
    combatant_id         bigint  PRIMARY KEY,
    encounter_id         bigint  NOT NULL,
    located_name         text    NOT NULL,
    real_name            text    NOT NULL,
    deaths               bigint  NOT NULL,
    action               text    NOT NULL,
    target_combatant_id  bigint  NOT NULL,
    row_json             text    NOT NULL
);
ALTER TABLE located_death_rows ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS idx_located_death_rows_encounter ON located_death_rows (encounter_id);

-- Breakdown rows that hung off a deleted located row. A located row exists
-- only as a death's victim and never attacks, so this is expected to stay
-- empty; the delete cascades to them, hence the copy.
CREATE TABLE IF NOT EXISTS located_death_children (
    source_table  text    NOT NULL,   -- attack_types | damage_types
    row_id        bigint  NOT NULL,
    combatant_id  bigint  NOT NULL,
    row_json      text    NOT NULL,
    PRIMARY KEY (source_table, row_id)
);
ALTER TABLE located_death_children ENABLE ROW LEVEL SECURITY;

-- 1a. Open a record for every encounter with a located row, and lock those
-- encounters in id order. A concurrent delete or hide then waits for this
-- transaction instead of interleaving with it.
INSERT INTO located_death_encounters (encounter_id, world, old_success_level, old_deaths, repaired_at)
SELECT e.id, e.world, e.success_level, e.deaths, floor(extract(epoch from now()))
FROM encounters e
WHERE e.id IN (
    SELECT c.encounter_id
    FROM combatants c
    JOIN encounters z ON z.id = c.encounter_id
    WHERE right(c.name, 1) = ')'
      AND c.name ~ ' in .+ \(-?[0-9.]+, -?[0-9.]+, -?[0-9.]+\)$'
      AND regexp_replace(c.name, ' in .+ \(-?[0-9.]+, -?[0-9.]+, -?[0-9.]+\)$', '') <> ''
      AND (COALESCE(z.zone, '') = ''
           OR substring(c.name from ' in (.+) \(-?[0-9.]+, -?[0-9.]+, -?[0-9.]+\)$') = z.zone))
ORDER BY e.id
FOR NO KEY UPDATE OF e
ON CONFLICT (encounter_id) DO NOTHING;

-- 1b. Record every located row of those encounters, whole, with what will
-- happen to it.
INSERT INTO located_death_rows
    (combatant_id, encounter_id, located_name, real_name, deaths, action, target_combatant_id, row_json)
SELECT s.id, s.encounter_id, s.name, s.real_name, s.deaths,
       CASE WHEN s.real_id IS NOT NULL THEN 'folded'
            WHEN s.id = s.keeper_id THEN 'renamed'
            ELSE 'merged' END,
       COALESCE(s.real_id, s.keeper_id),
       s.row_json
FROM (
    SELECT c.id, c.encounter_id, c.name, c.deaths, to_jsonb(c)::text AS row_json, x.real_name,
           (SELECT t.id FROM combatants t
            WHERE t.encounter_id = c.encounter_id AND t.name = x.real_name) AS real_id,
           min(c.id) OVER (PARTITION BY c.encounter_id, x.real_name) AS keeper_id
    FROM located_death_encounters r
    JOIN encounters e ON e.id = r.encounter_id
    JOIN combatants c ON c.encounter_id = e.id
    CROSS JOIN LATERAL (
        SELECT regexp_replace(c.name, ' in .+ \(-?[0-9.]+, -?[0-9.]+, -?[0-9.]+\)$', '') AS real_name) x
    WHERE r.new_success_level IS NULL
      AND right(c.name, 1) = ')'
      AND c.name ~ ' in .+ \(-?[0-9.]+, -?[0-9.]+, -?[0-9.]+\)$'
      AND x.real_name <> ''
      AND (COALESCE(e.zone, '') = ''
           OR substring(c.name from ' in (.+) \(-?[0-9.]+, -?[0-9.]+, -?[0-9.]+\)$') = e.zone)
) s
ON CONFLICT (combatant_id) DO NOTHING;

-- 1c. Copy the breakdown rows the deletes below would cascade away.
INSERT INTO located_death_children (source_table, row_id, combatant_id, row_json)
SELECT 'attack_types', a.id, a.combatant_id, to_jsonb(a)::text
FROM attack_types a
JOIN located_death_rows l ON l.combatant_id = a.combatant_id
JOIN located_death_encounters r ON r.encounter_id = l.encounter_id
WHERE r.new_success_level IS NULL AND l.action IN ('folded', 'merged')
ON CONFLICT (source_table, row_id) DO NOTHING;

INSERT INTO located_death_children (source_table, row_id, combatant_id, row_json)
SELECT 'damage_types', d.id, d.combatant_id, to_jsonb(d)::text
FROM damage_types d
JOIN located_death_rows l ON l.combatant_id = d.combatant_id
JOIN located_death_encounters r ON r.encounter_id = l.encounter_id
WHERE r.new_success_level IS NULL AND l.action IN ('folded', 'merged')
ON CONFLICT (source_table, row_id) DO NOTHING;

-- 2a. Fold: each target row gains the deaths of the located rows that point
-- at it.
UPDATE combatants c
SET deaths = c.deaths + s.added
FROM (
    SELECT l.target_combatant_id, sum(l.deaths)::bigint AS added
    FROM located_death_rows l
    JOIN located_death_encounters r ON r.encounter_id = l.encounter_id
    WHERE r.new_success_level IS NULL AND l.action IN ('folded', 'merged')
    GROUP BY l.target_combatant_id
) s
WHERE c.id = s.target_combatant_id;

-- 2b. The located rows whose deaths now live elsewhere go.
DELETE FROM combatants c
USING located_death_rows l, located_death_encounters r
WHERE c.id = l.combatant_id AND l.action IN ('folded', 'merged')
  AND r.encounter_id = l.encounter_id AND r.new_success_level IS NULL;

-- 2c. A located row with no real-name row becomes that row. It was
-- classified under a name with spaces in it (a pet, to the classifier), so
-- an allied one goes back to unclassified and the next read classifies the
-- encounter again.
UPDATE combatants c
SET name = l.real_name,
    is_player = CASE WHEN c.ally = 1 THEN NULL ELSE c.is_player END
FROM located_death_rows l, located_death_encounters r
WHERE c.id = l.combatant_id AND l.action = 'renamed'
  AND r.encounter_id = l.encounter_id AND r.new_success_level IS NULL;

-- 3. encounters.deaths is stored as the client sent it: the deaths of allies
-- with a player-shaped name (no space, not 'Unknown'). A located name has
-- spaces, so its deaths were never in that sum. Add the ones that now sit
-- on such a row. encounters.kills needs nothing: an ally's kill is counted
-- whatever the victim's name looks like.
UPDATE encounters e
SET deaths = e.deaths + s.added
FROM (
    SELECT l.encounter_id, sum(l.deaths)::bigint AS added
    FROM located_death_rows l
    JOIN located_death_encounters r ON r.encounter_id = l.encounter_id
    JOIN combatants t ON t.id = l.target_combatant_id
    WHERE r.new_success_level IS NULL
      AND t.ally = 1 AND position(' ' in l.real_name) = 0 AND l.real_name <> 'Unknown'
    GROUP BY l.encounter_id
) s
WHERE e.id = s.encounter_id;

-- 4. Outcome. The client's rule: win when its main enemy died and an allied
-- player never died. Only a stored partial (2) can become a win here.
--   * the main enemy is the mob the encounter is titled after, so the row
--     named exactly like the title must now show a death;
--   * the client picks that mob as the non-ally with the most damage taken
--     per death. Folding a second death into the title mob lowers its
--     score, so a fixed client could have picked another enemy. The last
--     condition keeps the upgrade to fights where the strongest enemy under
--     the client's formula is a dead one. It cannot fail when the title mob
--     died once: its score is then what it was when the client chose it;
--   * the survivor is an allied row with no death that the pet classifier
--     calls a player. is_player is stricter than the client's test (a name
--     without a space): it also drops auto-named pets and unresolved
--     extras. An encounter the classifier has not reached (is_player NULL)
--     falls back to the client's own test. A row that exists only because
--     of a death line (a renamed one) is never the survivor.
UPDATE encounters e
SET success_level = 1
FROM located_death_encounters r
WHERE r.encounter_id = e.id AND r.new_success_level IS NULL
  AND e.success_level = 2
  AND EXISTS (
      SELECT 1 FROM combatants t
      WHERE t.encounter_id = e.id AND t.name = e.title AND t.deaths > 0)
  AND EXISTS (
      SELECT 1 FROM combatants p
      WHERE p.encounter_id = e.id AND p.ally = 1 AND p.deaths = 0
        AND (p.is_player = 1
             OR (p.is_player IS NULL AND position(' ' in p.name) = 0 AND p.name <> 'Unknown'))
        AND NOT EXISTS (SELECT 1 FROM located_death_rows l WHERE l.combatant_id = p.id))
  AND COALESCE((SELECT max(y.damage_taken / y.deaths) FROM combatants y
                WHERE y.encounter_id = e.id AND y.ally = 0 AND y.deaths > 0), -1)
      >= COALESCE((SELECT max(y.damage_taken) FROM combatants y
                   WHERE y.encounter_id = e.id AND y.ally = 0 AND y.deaths <= 0), -1);

-- 5. A fight's ranking primary is its longest visible, verified, winning,
-- not ranking-barred upload (parses/fights.py _primaries; same ordering as
-- _canonical). Recompute it for the fights that just gained a win.
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
    WHERE r.new_success_level IS NULL AND e.fight_id IS NOT NULL
      AND e.success_level <> r.old_success_level);

-- 6. Close the records with what the encounters now hold.
UPDATE located_death_encounters r
SET new_success_level = e.success_level, new_deaths = e.deaths
FROM encounters e
WHERE e.id = r.encounter_id AND r.new_success_level IS NULL;
