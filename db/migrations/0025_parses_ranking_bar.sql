create schema if not exists parses;
set search_path to parses, public;

-- Permanent "never ranks" stamp. The 2026-10 spell-exploit window taints
-- every parse started inside it (user decision: lifting the embargo must
-- not let these parses onto the boards), independent of the spell-audit
-- hide/restore state. Stamped at ingest while the window is open
-- (backend/server/spell_audit.py: SPELL_AUDIT_SINCE .. SPELL_AUDIT_UNTIL)
-- and here for everything already stored. fights.primary_winning_encounter_id
-- never points at a stamped row (parses/fights.py), so the rankings never see one.
-- The spell-audit tables (0024, already applied in prod) missed the RLS rule.
ALTER TABLE flagged_characters ENABLE ROW LEVEL SECURITY;
ALTER TABLE spell_audit_scans ENABLE ROW LEVEL SECURITY;

ALTER TABLE encounters ADD COLUMN IF NOT EXISTS ranking_barred_at bigint;
ALTER TABLE encounters ADD COLUMN IF NOT EXISTS ranking_barred_reason text;
CREATE INDEX IF NOT EXISTS idx_encounters_ranking_barred ON encounters (world, started_at) WHERE ranking_barred_at IS NOT NULL;

UPDATE encounters
SET ranking_barred_at = floor(extract(epoch from now())), ranking_barred_reason = 'spell_exploit_window'
WHERE started_at >= 1791504000 AND ranking_barred_at IS NULL;

-- Fights whose ranking primary is now barred rank nothing (every member of
-- a fight starts within a minute of the others, so the whole fight is in
-- the window).
UPDATE fights SET primary_winning_encounter_id = NULL
WHERE primary_winning_encounter_id IN (SELECT id FROM encounters WHERE ranking_barred_at IS NOT NULL);
