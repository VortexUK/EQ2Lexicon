-- SQL for backend/eq2db/zones.py. Schema + DML. The shared `_meta` table
-- is created from backend/eq2db/_meta.sql via _meta.create_table().

-- ---------------------------------------------------------------------------
-- Schema
-- ---------------------------------------------------------------------------

-- :name schema_zones
CREATE TABLE IF NOT EXISTS zones (
    -- Identity
    id                      INTEGER PRIMARY KEY,
    name                    TEXT    NOT NULL UNIQUE,
    name_lower              TEXT    NOT NULL,

    -- Expansion attribution
    expansion_short         TEXT    NOT NULL,    -- 'DoF', 'AoM', 'CoE', ...
    expansion_name          TEXT    NOT NULL,    -- 'Desert of Flames', ...
    expansion_year          INTEGER,
    expansion_confidence    TEXT    NOT NULL,    -- 'category', 'live_update', ...
    expansion_source        TEXT,                -- audit trail / reason

    -- Flags
    is_persistent_instance  INTEGER NOT NULL DEFAULT 0,
    is_endless_persistent   INTEGER NOT NULL DEFAULT 0,
    is_tradeskill           INTEGER NOT NULL DEFAULT 0,
    is_pvp                  INTEGER NOT NULL DEFAULT 0,
    is_openworld            INTEGER NOT NULL DEFAULT 0,
    is_instance             INTEGER NOT NULL DEFAULT 0,
    is_live_event           INTEGER NOT NULL DEFAULT 0,
    is_city                 INTEGER NOT NULL DEFAULT 0,
    is_contested            INTEGER NOT NULL DEFAULT 0,
    is_deprecated           INTEGER NOT NULL DEFAULT 0,

    -- Optional metadata
    event_name              TEXT,                -- when is_live_event=1
    wiki_url                TEXT
);

-- Many-to-many zone ↔ type. A zone with both Solo and Group variants gets
-- two rows so a "all group zones in RoK" query is one indexed JOIN.
-- :name schema_zone_types
CREATE TABLE IF NOT EXISTS zone_types (
    zone_id  INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    type     TEXT    NOT NULL,
    PRIMARY KEY (zone_id, type)
);

-- Alias → canonical zone. ACT logs may emit "The Fabled Deathtoll" or
-- "Fabled Deathtoll"; find_by_name checks aliases before failing.
-- :name schema_zone_aliases
CREATE TABLE IF NOT EXISTS zone_aliases (
    alias        TEXT    NOT NULL PRIMARY KEY,
    alias_lower  TEXT    NOT NULL,
    zone_id      INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE
);

-- Raid encounters per zone — hand-curated. Each row is a named encounter
-- (1 mob solo, or 2-4 mobs grouped). encounter_name is the display label;
-- individual mob names live in zone_encounter_mobs for reverse lookup.
-- :name schema_zone_encounters
CREATE TABLE IF NOT EXISTS zone_encounters (
    id              INTEGER PRIMARY KEY,
    zone_id         INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    encounter_name  TEXT    NOT NULL,
    position        INTEGER NOT NULL,
    stage           TEXT,
    wiki_url        TEXT,
    UNIQUE (zone_id, position)
);

-- :name schema_zone_encounter_mobs
CREATE TABLE IF NOT EXISTS zone_encounter_mobs (
    id              INTEGER PRIMARY KEY,
    encounter_id    INTEGER NOT NULL REFERENCES zone_encounters(id) ON DELETE CASCADE,
    mob_name        TEXT    NOT NULL,
    mob_name_lower  TEXT    NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0
);

-- :name schema_featured_raid_expansions
CREATE TABLE IF NOT EXISTS featured_raid_expansions (
    expansion_short TEXT PRIMARY KEY,
    added_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER))
);

-- :name schema_featured_raid_zones
CREATE TABLE IF NOT EXISTS featured_raid_zones (
    zone_id INTEGER PRIMARY KEY REFERENCES zones(id) ON DELETE CASCADE,
    added_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER))
);

-- :name schema_featured_raid_categories
CREATE TABLE IF NOT EXISTS featured_raid_categories (
    expansion_short TEXT NOT NULL,
    name            TEXT NOT NULL,
    position        INTEGER NOT NULL,
    PRIMARY KEY (expansion_short, name)
);

-- :name indexes_all
CREATE INDEX IF NOT EXISTS idx_zones_name_lower    ON zones (name_lower);
CREATE INDEX IF NOT EXISTS idx_zones_expansion     ON zones (expansion_short);
CREATE INDEX IF NOT EXISTS idx_zones_event         ON zones (is_live_event, event_name);
CREATE INDEX IF NOT EXISTS idx_zones_tradeskill    ON zones (is_tradeskill);
CREATE INDEX IF NOT EXISTS idx_zone_types_type     ON zone_types (type);
CREATE INDEX IF NOT EXISTS idx_zone_types_zone     ON zone_types (zone_id);
CREATE INDEX IF NOT EXISTS idx_zone_aliases_lower  ON zone_aliases (alias_lower);
CREATE INDEX IF NOT EXISTS idx_zone_aliases_zone   ON zone_aliases (zone_id);
CREATE INDEX IF NOT EXISTS idx_zone_enc_zone       ON zone_encounters (zone_id, position);
CREATE INDEX IF NOT EXISTS idx_zone_enc_mobs_enc   ON zone_encounter_mobs (encounter_id, position);
CREATE INDEX IF NOT EXISTS idx_zone_enc_mobs_lower ON zone_encounter_mobs (mob_name_lower);

-- ---------------------------------------------------------------------------
-- Idempotent migrations + one-time normalisations
-- ---------------------------------------------------------------------------

-- :name migrate_add_featured_position
ALTER TABLE featured_raid_zones ADD COLUMN position INTEGER NOT NULL DEFAULT 0;

-- :name migrate_add_featured_category
ALTER TABLE featured_raid_zones ADD COLUMN category TEXT;

-- :name drop_legacy_zone_bosses
DROP TABLE IF EXISTS zone_bosses;

-- Legacy encounter_name values were the comma-joined display of every mob
-- ("Ire, Malevolence"). The roster editor treats encounter_name as the
-- primary mob's name. Rewrite any comma-containing row to its position-0
-- mob name. Idempotent: rows without commas don't match.
-- :name normalise_comma_joined_encounter_names
UPDATE zone_encounters
   SET encounter_name = (
           SELECT mob_name FROM zone_encounter_mobs m
            WHERE m.encounter_id = zone_encounters.id
            ORDER BY position ASC
            LIMIT 1
       )
 WHERE encounter_name LIKE '%,%'
   AND EXISTS (
           SELECT 1 FROM zone_encounter_mobs m
            WHERE m.encounter_id = zone_encounters.id
       );

-- Insert the parenthesised " (Zone)" form as an alias before stripping it
-- from the canonical name, so old references resolve.
-- :name normalise_paren_zone_to_alias
INSERT OR IGNORE INTO zone_aliases (alias, alias_lower, zone_id)
SELECT name, name_lower, id
  FROM zones
 WHERE name LIKE '% (Zone)%';

-- Strip the " (Zone)" suffix from canonical zone names.
-- :name normalise_strip_paren_zone
UPDATE zones
   SET name       = REPLACE(name,       ' (Zone)', ''),
       name_lower = REPLACE(name_lower, ' (zone)', '')
 WHERE name LIKE '% (Zone)%';

-- ---------------------------------------------------------------------------
-- Column-list fragments
-- ---------------------------------------------------------------------------

-- :name select_zone_cols
id, name, name_lower,
expansion_short, expansion_name, expansion_year,
expansion_confidence, expansion_source,
is_persistent_instance, is_endless_persistent,
is_tradeskill, is_pvp, is_openworld, is_instance,
is_live_event, is_city, is_contested, is_deprecated,
event_name, wiki_url

-- ---------------------------------------------------------------------------
-- Zone CRUD
-- ---------------------------------------------------------------------------

-- :name upsert_zone
INSERT INTO zones (
    name, name_lower,
    expansion_short, expansion_name, expansion_year,
    expansion_confidence, expansion_source,
    is_persistent_instance, is_endless_persistent,
    is_tradeskill, is_pvp, is_openworld, is_instance,
    is_live_event, is_city, is_contested, is_deprecated,
    event_name, wiki_url
) VALUES (
    :name, :name_lower,
    :expansion_short, :expansion_name, :expansion_year,
    :expansion_confidence, :expansion_source,
    :is_persistent_instance, :is_endless_persistent,
    :is_tradeskill, :is_pvp, :is_openworld, :is_instance,
    :is_live_event, :is_city, :is_contested, :is_deprecated,
    :event_name, :wiki_url
)
ON CONFLICT(name) DO UPDATE SET
    name_lower             = excluded.name_lower,
    expansion_short        = excluded.expansion_short,
    expansion_name         = excluded.expansion_name,
    expansion_year         = excluded.expansion_year,
    expansion_confidence   = excluded.expansion_confidence,
    expansion_source       = excluded.expansion_source,
    is_persistent_instance = excluded.is_persistent_instance,
    is_endless_persistent  = excluded.is_endless_persistent,
    is_tradeskill          = excluded.is_tradeskill,
    is_pvp                 = excluded.is_pvp,
    is_openworld           = excluded.is_openworld,
    is_instance            = excluded.is_instance,
    is_live_event          = excluded.is_live_event,
    is_city                = excluded.is_city,
    is_contested           = excluded.is_contested,
    is_deprecated          = excluded.is_deprecated,
    event_name             = excluded.event_name,
    wiki_url               = excluded.wiki_url;

-- :name select_zone_id_by_name
SELECT id FROM zones WHERE name = ?;

-- :name select_zone_name_and_expansion
SELECT name, expansion_short FROM zones WHERE id = ?;

-- :name select_zone_name_by_id
SELECT name FROM zones WHERE id = ?;

-- :name count_zones
SELECT COUNT(*) FROM zones;

-- :name find_zone_by_name_lower
SELECT {cols} FROM zones WHERE name_lower = ? LIMIT 1;

-- :name find_zone_by_id
SELECT {cols} FROM zones WHERE id = ?;

-- :name find_zone_id_by_alias
SELECT zone_id FROM zone_aliases WHERE alias_lower = ? LIMIT 1;

-- :name find_zone_id_by_alias_aliased
SELECT zone_id AS id FROM zone_aliases WHERE alias_lower = ?;

-- :name select_zone_id_by_name_lower
SELECT id FROM zones WHERE name_lower = ?;

-- :name list_zones_by_expansion
SELECT {cols} FROM zones WHERE expansion_short = ? ORDER BY name;

-- :name list_zones_by_expansion_typed
SELECT {cols} FROM zones
WHERE expansion_short = ?
  AND id IN (SELECT zone_id FROM zone_types WHERE type = ?)
ORDER BY name;

-- :name list_zones_by_event
SELECT {cols} FROM zones WHERE is_live_event = 1 AND event_name = ? ORDER BY name;

-- :name list_zones_by_type
SELECT {cols} FROM zones
WHERE id IN (SELECT zone_id FROM zone_types WHERE type = ?)
ORDER BY name;

-- :name list_zones_by_boss
SELECT {cols} FROM zones
WHERE id IN (
    SELECT e.zone_id FROM zone_encounters e
    INNER JOIN zone_encounter_mobs m ON m.encounter_id = e.id
    WHERE m.mob_name_lower = ?
)
ORDER BY name;

-- :name list_distinct_expansions
SELECT DISTINCT expansion_short, expansion_name, expansion_year
FROM zones
WHERE expansion_short IS NOT NULL
ORDER BY expansion_year DESC;

-- :name expansion_counts
SELECT expansion_short, COUNT(*) FROM zones GROUP BY expansion_short ORDER BY 2 DESC;

-- :name check_zone_exists_for_expansion
SELECT 1 FROM zones WHERE expansion_short = ? LIMIT 1;

-- ---------------------------------------------------------------------------
-- zone_types CRUD
-- ---------------------------------------------------------------------------

-- :name delete_zone_types_for_zone
DELETE FROM zone_types WHERE zone_id = ?;

-- :name insert_zone_type
INSERT INTO zone_types (zone_id, type) VALUES (?, ?);

-- :name insert_zone_type_or_ignore
INSERT OR IGNORE INTO zone_types (zone_id, type) VALUES (?, ?);

-- :name delete_zone_type
DELETE FROM zone_types WHERE zone_id = ? AND type = ?;

-- :name list_types_for_zone
SELECT type FROM zone_types WHERE zone_id = ? ORDER BY type;

-- :name check_zone_is_raid
SELECT 1 FROM zone_types WHERE zone_id = ? AND type IN ('raid_x4', 'raid_x2') LIMIT 1;

-- ---------------------------------------------------------------------------
-- zone_aliases CRUD
-- ---------------------------------------------------------------------------

-- :name delete_zone_aliases_for_zone
DELETE FROM zone_aliases WHERE zone_id = ?;

-- :name insert_zone_alias
INSERT INTO zone_aliases (alias, alias_lower, zone_id) VALUES (?, ?, ?);

-- :name list_aliases_for_zone
SELECT alias FROM zone_aliases WHERE zone_id = ? ORDER BY alias;

-- ---------------------------------------------------------------------------
-- zone_encounters CRUD
-- ---------------------------------------------------------------------------

-- :name delete_encounters_for_zone
DELETE FROM zone_encounters WHERE zone_id = ?;

-- :name insert_encounter
INSERT INTO zone_encounters (zone_id, encounter_name, position, stage, wiki_url) VALUES (?, ?, ?, ?, ?);

-- :name list_encounters_for_zone
SELECT id, encounter_name, position, stage, wiki_url
FROM zone_encounters WHERE zone_id = ? ORDER BY position;

-- :name select_encounter_by_id
SELECT id, zone_id, encounter_name, position, stage, wiki_url FROM zone_encounters WHERE id = ?;

-- :name select_encounter_zone_and_name
SELECT id, zone_id, encounter_name FROM zone_encounters WHERE id = ?;

-- :name select_encounter_zone_id
SELECT zone_id FROM zone_encounters WHERE id = ?;

-- :name update_encounter_meta
UPDATE zone_encounters SET encounter_name = ?, stage = ?, wiki_url = ? WHERE id = ?;

-- :name update_encounter_name
UPDATE zone_encounters SET encounter_name = ? WHERE id = ?;

-- :name update_encounter_position
UPDATE zone_encounters SET position = ? WHERE id = ?;

-- :name delete_encounter_by_id
DELETE FROM zone_encounters WHERE id = ?;

-- :name list_zone_encounter_positions
SELECT id, encounter_name, position FROM zone_encounters WHERE zone_id = ?;

-- :name max_encounter_position_for_zone
SELECT COALESCE(MAX(position), 0) + 1 AS p FROM zone_encounters WHERE zone_id = ?;

-- ---------------------------------------------------------------------------
-- zone_encounter_mobs CRUD
-- ---------------------------------------------------------------------------

-- :name insert_encounter_mob
INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower, position) VALUES (?, ?, ?, ?);

-- :name insert_encounter_mob_primary
INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower, position) VALUES (?, ?, ?, 0);

-- :name update_encounter_mob_primary_rename
UPDATE zone_encounter_mobs SET mob_name = ?, mob_name_lower = ?
WHERE encounter_id = ? AND position = 0;

-- :name list_mobs_for_encounter
SELECT id, mob_name, position FROM zone_encounter_mobs WHERE encounter_id = ? ORDER BY position;

-- :name list_mobs_for_encounter_asc
SELECT id, mob_name, position FROM zone_encounter_mobs WHERE encounter_id = ? ORDER BY position ASC;

-- :name list_mobs_for_encounter_names
SELECT mob_name, position FROM zone_encounter_mobs WHERE encounter_id = ? ORDER BY position ASC;

-- :name select_primary_mob_name
SELECT mob_name FROM zone_encounter_mobs WHERE encounter_id = ? AND position = 0;

-- :name select_primary_mob_id_and_name
SELECT id, mob_name FROM zone_encounter_mobs WHERE encounter_id = ? AND position = 0;

-- :name select_mob_by_id
SELECT id, mob_name, position FROM zone_encounter_mobs WHERE id = ?;

-- :name select_mob_for_update
SELECT encounter_id, mob_name, position FROM zone_encounter_mobs WHERE id = ?;

-- :name select_mob_for_promote
SELECT id, encounter_id, mob_name, position FROM zone_encounter_mobs WHERE id = ?;

-- :name select_mob_encounter_position
SELECT id, encounter_id, position FROM zone_encounter_mobs WHERE id = ?;

-- :name update_mob_name
UPDATE zone_encounter_mobs SET mob_name = ?, mob_name_lower = ? WHERE id = ?;

-- :name update_mob_position
UPDATE zone_encounter_mobs SET position = ? WHERE id = ?;

-- :name update_mob_position_to_zero
UPDATE zone_encounter_mobs SET position = 0 WHERE id = ?;

-- :name update_mob_position_to_neg_one
UPDATE zone_encounter_mobs SET position = -1 WHERE id = ?;

-- :name shift_mobs_negative
UPDATE zone_encounter_mobs SET position = -position - 1 WHERE encounter_id = ?;

-- :name shift_mobs_back_positive
UPDATE zone_encounter_mobs SET position = -position WHERE encounter_id = ?;

-- :name max_mob_position_for_encounter
SELECT COALESCE(MAX(position), -1) + 1 FROM zone_encounter_mobs WHERE encounter_id = ?;

-- :name count_mobs_for_encounter
SELECT COUNT(*) FROM zone_encounter_mobs WHERE encounter_id = ?;

-- :name delete_mob_by_id
DELETE FROM zone_encounter_mobs WHERE id = ?;

-- ---------------------------------------------------------------------------
-- featured_raid_* CRUD
-- ---------------------------------------------------------------------------

-- :name list_featured_raid_expansions
WITH all_shorts AS (
    SELECT expansion_short AS short FROM featured_raid_expansions
    UNION
    SELECT DISTINCT z.expansion_short AS short
    FROM featured_raid_zones f
    JOIN zones z ON z.id = f.zone_id
    WHERE z.expansion_short IS NOT NULL
)
SELECT DISTINCT z.expansion_short AS short,
                z.expansion_name  AS name,
                z.expansion_year  AS year
FROM zones z
JOIN all_shorts s ON s.short = z.expansion_short
WHERE z.expansion_short IS NOT NULL
ORDER BY z.expansion_year DESC, z.expansion_short;

-- :name list_available_raid_expansions
SELECT DISTINCT z.expansion_short AS short,
                z.expansion_name  AS name,
                z.expansion_year  AS year
FROM zones z
WHERE z.expansion_short IS NOT NULL
  AND z.expansion_short NOT IN (SELECT expansion_short FROM featured_raid_expansions)
  AND z.expansion_short NOT IN (
      SELECT DISTINCT z2.expansion_short
      FROM featured_raid_zones f
      JOIN zones z2 ON z2.id = f.zone_id
      WHERE z2.expansion_short IS NOT NULL
  )
ORDER BY z.expansion_year DESC, z.expansion_short;

-- :name insert_featured_raid_expansion
INSERT OR IGNORE INTO featured_raid_expansions (expansion_short) VALUES (?);

-- :name remove_featured_raid_zones_in_expansion
DELETE FROM featured_raid_zones
 WHERE zone_id IN (
     SELECT id FROM zones WHERE expansion_short = ?
 );

-- :name delete_featured_raid_expansion
DELETE FROM featured_raid_expansions WHERE expansion_short = ?;

-- :name list_featured_raid_zones
SELECT {cols},
       f.position AS featured_position,
       f.category AS featured_category
FROM zones z
JOIN featured_raid_zones f ON f.zone_id = z.id
WHERE z.expansion_short = ?
ORDER BY f.category, f.position;

-- :name list_available_raid_zones
SELECT DISTINCT {cols}
FROM zones z
JOIN zone_types t ON t.zone_id = z.id
WHERE z.expansion_short = ?
  AND t.type IN ('raid_x4', 'raid_x2')
  AND z.id NOT IN (SELECT zone_id FROM featured_raid_zones)
ORDER BY z.name;

-- :name max_featured_position_uncategorised
SELECT COALESCE(MAX(f.position), -1)
FROM featured_raid_zones f
JOIN zones z2 ON z2.id = f.zone_id
WHERE z2.expansion_short = ? AND f.category IS NULL;

-- :name insert_featured_raid_zone_uncategorised
INSERT OR IGNORE INTO featured_raid_zones (zone_id, position, category) VALUES (?, ?, NULL);

-- :name delete_featured_raid_zone_by_name
DELETE FROM featured_raid_zones
 WHERE zone_id = (SELECT id FROM zones WHERE name_lower = ? LIMIT 1);

-- :name find_featured_zone_id_in_expansion
SELECT z.id FROM zones z
JOIN featured_raid_zones f ON f.zone_id = z.id
WHERE z.name_lower = ? AND z.expansion_short = ?;

-- :name max_category_position
SELECT COALESCE(MAX(position), -1) FROM featured_raid_categories WHERE expansion_short = ?;

-- :name insert_featured_raid_category_or_ignore
INSERT OR IGNORE INTO featured_raid_categories (expansion_short, name, position) VALUES (?, ?, ?);

-- :name insert_featured_raid_category
INSERT INTO featured_raid_categories (expansion_short, name, position) VALUES (?, ?, ?);

-- :name check_featured_raid_category_exists
SELECT 1 FROM featured_raid_categories WHERE expansion_short = ? AND name = ?;

-- :name update_featured_raid_zone_position_and_category
UPDATE featured_raid_zones SET position = ?, category = ? WHERE zone_id = ?;

-- :name update_featured_raid_zone_position
UPDATE featured_raid_zones SET position = ? WHERE zone_id = ?;

-- :name update_featured_raid_category_position
UPDATE featured_raid_categories SET position = ? WHERE expansion_short = ? AND name = ?;

-- :name list_featured_raid_categories
SELECT name, position FROM featured_raid_categories WHERE expansion_short = ? ORDER BY position;

-- :name move_featured_zones_to_null_category
UPDATE featured_raid_zones SET category = NULL
WHERE category = ?
  AND zone_id IN (SELECT id FROM zones WHERE expansion_short = ?);

-- :name delete_featured_raid_category
DELETE FROM featured_raid_categories WHERE expansion_short = ? AND name = ?;
