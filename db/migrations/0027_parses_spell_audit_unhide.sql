create schema if not exists parses;
set search_path to parses, public;

-- The spell audit must only keep parses off the RANKINGS. Its first cut hid
-- them from the list as well. Restore every parse it hid (they keep, or
-- gain, the ranking_barred stamp) and give the affected fights their list
-- primary back. Fights first, while hidden_by still identifies the rows.
UPDATE fights f
SET primary_encounter_id = (
        SELECT e.id FROM encounters e
        WHERE e.fight_id = f.id AND (e.hidden_at IS NULL OR e.hidden_by = 'spell-audit')
        ORDER BY e.duration_s DESC, e.started_at ASC, e.id ASC LIMIT 1),
    updated_at = floor(extract(epoch from now()))
WHERE f.id IN (SELECT fight_id FROM encounters WHERE hidden_by = 'spell-audit' AND fight_id IS NOT NULL);

UPDATE encounters
SET hidden_at = NULL, hidden_by = NULL,
    ranking_barred_at = COALESCE(ranking_barred_at, floor(extract(epoch from now()))),
    ranking_barred_reason = COALESCE(ranking_barred_reason, 'flagged_character')
WHERE hidden_by = 'spell-audit';
