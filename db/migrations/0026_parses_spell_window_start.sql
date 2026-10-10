create schema if not exists parses;
set search_path to parses, public;

-- The spell-exploit window started at 00:01 Pacific on 2026-10-09, which is
-- 07:01 UTC (1791529260). The first cut used midnight UTC (1791504000), so
-- seven hours of pre-event parses were tainted, and some were hidden by
-- the spell audit. Reverse exactly that stretch:
--   1. drop the window stamp from parses started before the real start;
--   2. restore parses the audit hid in that stretch, and remove the tamper
--      reports it filed for them;
--   3. recompute the affected fights' primaries so they rank again.
-- Idempotent: every statement is a no-op once applied.
-- SPELL_AUDIT_SINCE's default moved to 07:01 UTC in the same deploy, so
-- the sweep and ingest enforce from the same instant.

UPDATE encounters
SET ranking_barred_at = NULL, ranking_barred_reason = NULL
WHERE ranking_barred_reason = 'spell_exploit_window'
  AND started_at >= 1791504000 AND started_at < 1791529260;

DELETE FROM tamper_reports t
USING encounters e
WHERE t.reason = 'server_out_of_era_spells'
  AND e.world = t.world AND e.act_encid = t.act_encid
  AND e.hidden_by = 'spell-audit'
  AND e.started_at >= 1791504000 AND e.started_at < 1791529260;

UPDATE encounters
SET hidden_at = NULL, hidden_by = NULL
WHERE hidden_by = 'spell-audit'
  AND started_at >= 1791504000 AND started_at < 1791529260;

-- Same ordering as parses/fights.py _canonical: longest, then earliest, then lowest id.
UPDATE fights f
SET primary_encounter_id = (
        SELECT e.id FROM encounters e
        WHERE e.fight_id = f.id AND e.hidden_at IS NULL
        ORDER BY e.duration_s DESC, e.started_at ASC, e.id ASC LIMIT 1),
    primary_winning_encounter_id = (
        SELECT e.id FROM encounters e
        WHERE e.fight_id = f.id AND e.hidden_at IS NULL AND e.uploader_verified = 1
          AND e.success_level = 1 AND e.ranking_barred_at IS NULL
        ORDER BY e.duration_s DESC, e.started_at ASC, e.id ASC LIMIT 1),
    updated_at = floor(extract(epoch from now()))
WHERE f.id IN (
    SELECT DISTINCT fight_id FROM encounters
    WHERE fight_id IS NOT NULL AND started_at >= 1791504000 AND started_at < 1791529260);
