-- SQL for backend/eq2db/raids.py (psycopg, raids schema). DML only —
-- schema DDL lives in db/migrations/0005_raids.sql.

-- ---------------------------------------------------------------------------
-- raid_zones
-- ---------------------------------------------------------------------------

-- :name select_zone_by_name
SELECT id, source FROM raid_zones WHERE zone_name = %s;

-- :name select_zone_id_by_name
SELECT id FROM raid_zones WHERE zone_name = %s;

-- Refresh wiki-owned columns on an existing human-edited row.
-- :name update_zone_wiki_fields
UPDATE raid_zones SET
    expansion_short = %s,
    wiki_url        = %s,
    level_range     = %s,
    zdiff           = %s,
    lockout_min     = %s,
    lockout_max     = %s,
    last_synced_at  = %s
WHERE id = %s;

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
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
SELECT id, strategy_md FROM raid_encounters WHERE raid_zone_id = %s AND mob_name_lower = %s;

-- :name select_encounter_source
SELECT source FROM raid_encounters WHERE id = %s;

-- :name insert_encounter
INSERT INTO raid_encounters (
    raid_zone_id, mob_name, mob_name_lower, position,
    strategy_md, wiki_url, source,
    last_synced_at, last_edited_at, last_edited_by
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id;

-- Refresh sync timestamp + URL + position on a manually-edited row, leaving
-- strategy_md alone — re-scrape doesn't overwrite curator edits.
-- :name update_encounter_url_position_synced
UPDATE raid_encounters SET wiki_url = %s, position = %s, last_synced_at = %s WHERE id = %s;

-- Big conditional update: only the SCRAPE actor refreshes last_synced_at;
-- only non-SCRAPE actors stamp last_edited_at + last_edited_by. The CASE
-- WHEN guards keep the wrong column from being clobbered. The ::text casts
-- let Postgres type the parameter-to-parameter comparisons (both sides are
-- source tokens).
-- :name update_encounter
UPDATE raid_encounters SET
    mob_name        = %s,
    position        = %s,
    strategy_md     = COALESCE(%s, strategy_md),
    wiki_url        = %s,
    source          = %s,
    last_synced_at  = CASE WHEN %s::text = %s::text THEN %s ELSE last_synced_at END,
    last_edited_at  = CASE WHEN %s::text <> %s::text THEN %s ELSE last_edited_at END,
    last_edited_by  = CASE WHEN %s::text <> %s::text THEN %s ELSE last_edited_by END
WHERE id = %s;

-- :name rename_encounter_by_zone_mob
UPDATE raid_encounters
   SET mob_name = %s,
       mob_name_lower = %s,
       last_edited_at = floor(extract(epoch from now()))::bigint
 WHERE id IN (
     SELECT re.id FROM raid_encounters re
     JOIN raid_zones rz ON rz.id = re.raid_zone_id
     WHERE rz.zone_name_lower = %s
       AND re.mob_name_lower = %s
 );

-- :name update_encounter_position_by_zone_mob
UPDATE raid_encounters
   SET position = %s,
       last_edited_at = floor(extract(epoch from now()))::bigint
 WHERE id IN (
     SELECT re.id FROM raid_encounters re
     JOIN raid_zones rz ON rz.id = re.raid_zone_id
     WHERE rz.zone_name_lower = %s
       AND re.mob_name_lower = %s
 );

-- :name delete_encounter_by_zone_mob
DELETE FROM raid_encounters
 WHERE id IN (
     SELECT re.id FROM raid_encounters re
     JOIN raid_zones rz ON rz.id = re.raid_zone_id
     WHERE rz.zone_name_lower = %s
       AND re.mob_name_lower = %s
 );

-- ---------------------------------------------------------------------------
-- raid_encounter_revisions
-- ---------------------------------------------------------------------------

-- before_md may be NULL (first-ever revision) — caller passes None and the
-- driver handles the binding. One INSERT covers both seeding and updates.
-- :name insert_encounter_revision
INSERT INTO raid_encounter_revisions
(encounter_id, edited_at, edited_by, before_md, after_md, edit_note)
VALUES (%s, %s, %s, %s, %s, %s);

-- ---------------------------------------------------------------------------
-- Read helpers
-- ---------------------------------------------------------------------------

-- :name list_encounter_revisions
SELECT id, encounter_id, edited_at, edited_by, before_md, after_md, edit_note
FROM raid_encounter_revisions
WHERE encounter_id = %s ORDER BY edited_at DESC, id DESC;

-- :name list_zone_revisions
SELECT id, edited_at, edited_by, before_md, after_md, edit_note
FROM raid_zone_revisions WHERE raid_zone_id = %s
ORDER BY edited_at DESC, id DESC;
