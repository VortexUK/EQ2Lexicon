-- SQL for backend/server/api/parses/list.py — encounter listing + detail.
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
WHERE encounter_id = ? AND is_player = 1
ORDER BY encdps DESC, name ASC
LIMIT ?;

-- :name all_ally_names
SELECT name FROM combatants
WHERE encounter_id = ? AND is_player = 1;

-- ---------------------------------------------------------------------------
-- Lazy combatant-classification trigger
-- ---------------------------------------------------------------------------

-- :name has_unclassified_combatants
-- Cheap probe: does this encounter still have any combatant rows with
-- is_player IS NULL? Drives the pre-Phase-4 lazy backfill.
SELECT 1 FROM combatants
WHERE encounter_id = ? AND is_player IS NULL LIMIT 1;

-- ---------------------------------------------------------------------------
-- Encounter list + detail
-- ---------------------------------------------------------------------------

-- :name list_encounters_recent
-- Encounter rows most-recent-first, capped at the inner ? limit. Caller
-- composes the WHERE filter from request params and templates both
-- where_sql (the full "WHERE ... AND ...") and player_count_sql (the
-- correlated subquery) in via .format().
SELECT * FROM (
    SELECT e.*,
        ({player_count_sql}) AS player_count,
        (SELECT COUNT(*) FROM combatants c2 WHERE c2.encounter_id = e.id) AS combatant_count
    FROM encounters e
)
{where_sql}
ORDER BY started_at DESC
LIMIT ?;

-- :name select_encounter_by_id_and_world
-- World-scoped fetch by encounter id. The world scope keeps a viewer on
-- one server from reading another server's encounter by guessing its
-- integer id.
SELECT * FROM encounters WHERE id = ? AND world = ?;
