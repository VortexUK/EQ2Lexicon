-- SQL queries for backend/eq2db/raids.py.
-- DDL (CREATE TABLE / INDEX) stays in raids.py next to init_db().

-- ---------------------------------------------------------------------------
-- raid_zones
-- ---------------------------------------------------------------------------

-- :name select_zone_by_name
SELECT id, source FROM raid_zones WHERE zone_name = ?;

-- :name select_zone_id_by_name
SELECT id FROM raid_zones WHERE zone_name = ?;

-- Refresh wiki-owned columns on an existing human-edited row.
-- :name update_zone_wiki_fields
UPDATE raid_zones SET
    expansion_short = ?,
    wiki_url        = ?,
    level_range     = ?,
    zdiff           = ?,
    lockout_min     = ?,
    lockout_max     = ?,
    last_synced_at  = ?
WHERE id = ?;

-- COALESCE-on-conflict so a None caller param means "don't touch", not
-- "clobber to NULL". See the long comment in raids.py:upsert_raid_zone for
-- the history (curators losing zone overviews on encounter edits).
-- :name upsert_zone
INSERT INTO raid_zones (
    zone_name, zone_name_lower,
    expansion_short, wiki_url,
    access_md, background_md, overview_md,
    level_range, zdiff, lockout_min, lockout_max,
    source, last_synced_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(zone_name) DO UPDATE SET
    expansion_short = COALESCE(excluded.expansion_short, raid_zones.expansion_short),
    wiki_url        = COALESCE(excluded.wiki_url,        raid_zones.wiki_url),
    access_md       = COALESCE(excluded.access_md,       raid_zones.access_md),
    background_md   = COALESCE(excluded.background_md,   raid_zones.background_md),
    overview_md     = COALESCE(excluded.overview_md,     raid_zones.overview_md),
    level_range     = COALESCE(excluded.level_range,     raid_zones.level_range),
    zdiff           = COALESCE(excluded.zdiff,           raid_zones.zdiff),
    lockout_min     = COALESCE(excluded.lockout_min,     raid_zones.lockout_min),
    lockout_max     = COALESCE(excluded.lockout_max,     raid_zones.lockout_max),
    source          = excluded.source,
    last_synced_at  = COALESCE(excluded.last_synced_at,  raid_zones.last_synced_at);

-- ---------------------------------------------------------------------------
-- raid_encounters
-- ---------------------------------------------------------------------------

-- :name select_encounter_by_zone_mob
SELECT id, strategy_md FROM raid_encounters WHERE raid_zone_id = ? AND mob_name_lower = ?;

-- :name select_encounter_source
SELECT source FROM raid_encounters WHERE id = ?;

-- :name insert_encounter
INSERT INTO raid_encounters (
    raid_zone_id, mob_name, mob_name_lower, position,
    strategy_md, wiki_url, source,
    last_synced_at, last_edited_at, last_edited_by
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);

-- Refresh sync timestamp + URL + position on a manually-edited row, leaving
-- strategy_md alone — re-scrape doesn't overwrite curator edits.
-- :name update_encounter_url_position_synced
UPDATE raid_encounters SET wiki_url = ?, position = ?, last_synced_at = ? WHERE id = ?;

-- Big conditional update: only the SCRAPE actor refreshes last_synced_at;
-- only non-SCRAPE actors stamp last_edited_at + last_edited_by. The CASE
-- WHEN guards keep the wrong column from being clobbered.
-- :name update_encounter
UPDATE raid_encounters SET
    mob_name        = ?,
    position        = ?,
    strategy_md     = COALESCE(?, strategy_md),
    wiki_url        = ?,
    source          = ?,
    last_synced_at  = CASE WHEN ?=? THEN ? ELSE last_synced_at END,
    last_edited_at  = CASE WHEN ?<>? THEN ? ELSE last_edited_at END,
    last_edited_by  = CASE WHEN ?<>? THEN ? ELSE last_edited_by END
WHERE id = ?;

-- :name rename_encounter_by_zone_mob
UPDATE raid_encounters
   SET mob_name = ?,
       mob_name_lower = ?,
       last_edited_at = strftime('%s','now')
 WHERE id IN (
     SELECT re.id FROM raid_encounters re
     JOIN raid_zones rz ON rz.id = re.raid_zone_id
     WHERE rz.zone_name_lower = ?
       AND re.mob_name_lower = ?
 );

-- :name update_encounter_position_by_zone_mob
UPDATE raid_encounters
   SET position = ?,
       last_edited_at = strftime('%s','now')
 WHERE id IN (
     SELECT re.id FROM raid_encounters re
     JOIN raid_zones rz ON rz.id = re.raid_zone_id
     WHERE rz.zone_name_lower = ?
       AND re.mob_name_lower = ?
 );

-- :name delete_encounter_by_zone_mob
DELETE FROM raid_encounters
 WHERE id IN (
     SELECT re.id FROM raid_encounters re
     JOIN raid_zones rz ON rz.id = re.raid_zone_id
     WHERE rz.zone_name_lower = ?
       AND re.mob_name_lower = ?
 );

-- ---------------------------------------------------------------------------
-- raid_encounter_revisions
-- ---------------------------------------------------------------------------

-- before_md may be NULL (first-ever revision) — caller passes None and SQLite
-- handles the binding. One INSERT covers both seeding and updates.
-- :name insert_encounter_revision
INSERT INTO raid_encounter_revisions
(encounter_id, edited_at, edited_by, before_md, after_md, edit_note)
VALUES (?, ?, ?, ?, ?, ?);

-- ---------------------------------------------------------------------------
-- Read helpers
-- ---------------------------------------------------------------------------

-- Column-list fragments used by find_zone_by_name + list_zones_by_expansion.
-- :name select_zone_cols
id, zone_name, zone_name_lower, expansion_short, wiki_url,
access_md, background_md, overview_md,
level_range, zdiff, lockout_min, lockout_max,
source, last_synced_at, last_edited_at, last_edited_by

-- :name select_encounter_cols
id, raid_zone_id, mob_name, mob_name_lower, position,
strategy_md, wiki_url, source, last_synced_at, last_edited_at, last_edited_by

-- :name find_zone_by_name_ci
SELECT {cols} FROM raid_zones WHERE zone_name_lower = ?;

-- :name list_encounters_for_zone
SELECT {cols} FROM raid_encounters WHERE raid_zone_id = ? ORDER BY position, mob_name;

-- :name list_zones_by_expansion
SELECT {cols} FROM raid_zones WHERE expansion_short = ? ORDER BY zone_name;

-- :name list_encounter_revisions
SELECT id, encounter_id, edited_at, edited_by, before_md, after_md, edit_note
FROM raid_encounter_revisions
WHERE encounter_id = ? ORDER BY edited_at DESC, id DESC;

-- :name list_zone_revisions
SELECT id, edited_at, edited_by, before_md, after_md, edit_note
FROM raid_zone_revisions WHERE raid_zone_id = ?
ORDER BY edited_at DESC, id DESC;

-- ---------------------------------------------------------------------------
-- Stats
-- ---------------------------------------------------------------------------

-- :name stats_zones_count
SELECT COUNT(*) FROM raid_zones;

-- :name stats_encounters_count
SELECT COUNT(*) FROM raid_encounters;

-- :name stats_revisions_count
SELECT COUNT(*) FROM raid_encounter_revisions;

-- :name stats_encounters_by_source
SELECT source, COUNT(*) FROM raid_encounters GROUP BY source;

-- :name stats_zones_by_expansion
SELECT expansion_short, COUNT(*) FROM raid_zones GROUP BY expansion_short ORDER BY 2 DESC;
