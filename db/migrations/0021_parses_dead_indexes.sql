create schema if not exists parses;
set search_path to parses, public;

-- Two combatants indexes no query reads (zero scans in the 36 hours after the
-- 2026-10-07 cutover, pg_stat_user_indexes): nothing filters combatants by
-- name alone, and (encounter_id, ally) is covered by the rankings covering
-- index plus the partial unclassified index. combatants is the hottest
-- ingest table, so every upload paid to maintain them for no reader.
DROP INDEX IF EXISTS idx_combatants_name;
DROP INDEX IF EXISTS idx_combatants_ally;
