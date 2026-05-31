-- SQL for backend/server/api/raid_strategies.py — encounter / zone
-- overview + strategy read/write + revisions.
--
-- All queries are fixed shapes (no dynamic WHERE assembly). Several
-- queries appear in multiple Python helpers because each runs in its
-- own short-lived connection; consolidating them at the .sql level
-- removes hand-duplication without changing call sites.

-- ---------------------------------------------------------------------------
-- raid_zones lookups
-- ---------------------------------------------------------------------------

-- :name select_raid_zone_id_by_name
SELECT id FROM raid_zones WHERE zone_name_lower = ?;

-- :name select_raid_zone_id_and_overview
SELECT id, overview_md FROM raid_zones WHERE zone_name_lower = ?;

-- :name select_raid_zone_overview
SELECT zone_name, overview_md, source, last_edited_at, last_edited_by
FROM raid_zones WHERE zone_name_lower = ?;

-- ---------------------------------------------------------------------------
-- raid_zones overview writes
-- ---------------------------------------------------------------------------

-- :name update_raid_zone_audit_fields
-- Stamps last_edited_at + last_edited_by after the upsert_raid_zone helper
-- runs (which doesn't touch the audit columns itself).
UPDATE raid_zones SET last_edited_at = ?, last_edited_by = ?
WHERE zone_name_lower = ?;

-- :name update_raid_zone_overview
-- Existing-row overview replace + audit-field bump in one statement.
UPDATE raid_zones SET
    overview_md = ?,
    source = ?,
    last_edited_at = ?,
    last_edited_by = ?
WHERE id = ?;

-- ---------------------------------------------------------------------------
-- raid_zone_revisions
-- ---------------------------------------------------------------------------

-- :name insert_raid_zone_revision_first
-- First-ever revision for a zone overview — before_md is intentionally NULL.
INSERT INTO raid_zone_revisions
    (raid_zone_id, edited_at, edited_by, before_md, after_md, edit_note)
VALUES (?, ?, ?, NULL, ?, ?);

-- :name insert_raid_zone_revision
-- Subsequent revisions carry the previous markdown in before_md.
INSERT INTO raid_zone_revisions
    (raid_zone_id, edited_at, edited_by, before_md, after_md, edit_note)
VALUES (?, ?, ?, ?, ?, ?);

-- ---------------------------------------------------------------------------
-- raid_encounters
-- ---------------------------------------------------------------------------

-- :name select_encounter_id_by_zone_mob
SELECT id FROM raid_encounters WHERE raid_zone_id = ? AND mob_name_lower = ?;

-- :name select_encounter_strategy
-- Full strategy row for an encounter — used by both the read path and the
-- post-write re-read so the response carries the merged shape.
SELECT id, mob_name, position, strategy_md, source,
       last_edited_at, last_edited_by
FROM raid_encounters
WHERE raid_zone_id = ? AND mob_name_lower = ?;
