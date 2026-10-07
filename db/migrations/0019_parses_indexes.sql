create schema if not exists parses;
set search_path to parses, public;

-- The retention sweep's encounter deletes cascade into ingest_log through an
-- unindexed FK: 333 sequential scans for 321 deletes in one day.
CREATE INDEX IF NOT EXISTS idx_ingest_log_encounter ON ingest_log (encounter_id);

-- The lazy-classification probe (which ally rows still have is_player NULL
-- for these encounters?) was 40% of all statements and always empty. This
-- partial index makes it an empty-index lookup instead of filtering ~386k
-- entries per call.
CREATE INDEX IF NOT EXISTS idx_combatants_unclassified
    ON combatants (encounter_id) WHERE ally = 1 AND is_player IS NULL;
