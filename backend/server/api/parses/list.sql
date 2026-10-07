-- SQL for backend/server/api/parses/list.py — encounter listing + detail
-- (psycopg, parses schema).
--
-- player_count_subquery is exposed as a Python name (_PLAYER_COUNT_SQL) for
-- rankings.py which composes it into its own .sql via .format() — keep it
-- as a standalone block so the two callers share one source of truth.
--
-- list_encounters_recent uses two template parameters (where_sql,
-- player_count_sql) because the WHERE filter is built dynamically from
-- (zone, size, world) request params and the player_count subquery is
-- shared with rankings.py. The other two _ALLY queries are static.

-- ---------------------------------------------------------------------------
-- Shared subquery fragments
-- ---------------------------------------------------------------------------

-- :name player_count_subquery
-- Correlated subquery that counts player rows for an encounter aliased `e`.
-- Composed into the outer SELECT of list_encounters_recent via .format() —
-- and re-used by rankings.py via the _PLAYER_COUNT_SQL Python name.
SELECT COUNT(*) FROM combatants c
WHERE c.encounter_id = e.id AND c.is_player = 1

-- ---------------------------------------------------------------------------
-- Ally name lookups (used by the mirror-group top-N gate)
-- ---------------------------------------------------------------------------

-- :name top_n_ally_names
-- Top-N players in an encounter ordered by encDPS DESC, name ASC for
-- deterministic tiebreaks. Used by the merger to evaluate mutual
-- containment of two uploads' top-N sets.
SELECT name FROM combatants
WHERE encounter_id = %s AND is_player = 1
ORDER BY encdps DESC, name ASC
LIMIT %s;

-- :name all_ally_names
SELECT name FROM combatants
WHERE encounter_id = %s AND is_player = 1;

-- :name ally_rosters_bulk
-- Every candidate encounter's player roster in ONE statement, already in
-- top-N order (encDPS DESC, name ASC) so the grouper slices [:n] for the
-- top-N set and uses the whole list for the containment set. Replaces the
-- four lookups per compared pair (twelve percent of all production statements).
SELECT encounter_id, name FROM combatants
WHERE encounter_id = ANY(%s) AND is_player = 1
ORDER BY encounter_id, encdps DESC, name ASC;

-- ---------------------------------------------------------------------------
-- Lazy combatant-classification trigger
-- ---------------------------------------------------------------------------

-- :name has_unclassified_combatants
-- Cheap probe: does this encounter still have any ALLY combatant rows with
-- is_player IS NULL? Drives the pre-Phase-4 lazy backfill. Scoped to
-- ally = 1 because that is exactly the set classify_combatants can ever
-- classify — enemy rows keep is_player NULL by design (omitted from the
-- classifier's result), so probing all rows made every encounter look
-- perpetually unclassified: every read path re-ran the classifier and
-- re-WROTE the same ally rows forever, which is what melted the rankings
-- load (thousands of write+commit cycles per cold cache fill, colliding
-- with raid-night ingest → "database is locked").
SELECT 1 FROM combatants
WHERE encounter_id = %s AND ally = 1 AND is_player IS NULL LIMIT 1;

-- :name encounters_with_unclassified_combatants
-- Batched form of has_unclassified_combatants: the rankings rebuild probes
-- its whole candidate set in ONE = ANY query instead of one probe per encounter.
SELECT DISTINCT encounter_id FROM combatants
WHERE encounter_id = ANY(%s) AND ally = 1 AND is_player IS NULL;

-- ---------------------------------------------------------------------------
-- Encounter list + detail
-- ---------------------------------------------------------------------------

-- Encounter rows most-recent-first, capped at the inner %s limit. Caller
-- composes the WHERE filter from request params and templates both
-- where_sql (the full "WHERE ... AND ...") and player_count_sql (the
-- correlated subquery) in via .format(). (Comment kept OUTSIDE the block —
-- the composed where_sql contains %s and psycopg counts placeholders
-- inside comments.)
-- :name list_encounters_recent
SELECT * FROM (
    SELECT e.*,
        ({player_count_sql}) AS player_count,
        (SELECT COUNT(*) FROM combatants c2 WHERE c2.encounter_id = e.id) AS combatant_count
    FROM encounters e
) sub
{where_sql}
ORDER BY started_at DESC
LIMIT %s;

-- :name select_encounter_by_id_and_world
-- World-scoped fetch by encounter id. The world scope keeps a viewer on
-- one server from reading another server's encounter by guessing its
-- integer id.
SELECT * FROM encounters WHERE id = %s AND world = %s;
