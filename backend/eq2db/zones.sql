-- SQL for backend/eq2db/zones.py — DML against the Postgres `zones` schema.
-- Schema DDL lives in db/migrations/0004_zones.sql (migrations own DDL; the
-- catalogue never creates tables). psycopg %s / %(name)s placeholders.
--
-- Blocks containing the `{cols}` format field are composed with
-- `.format(cols=_SELECT_COLS)` at call time — keep their bodies brace-free
-- and comment-free so str.format can't trip on stray `{`/`}`.

-- ---------------------------------------------------------------------------
-- _meta provenance (zones-schema copy of the shared eq2db helper; the shared
-- backend/eq2db/_meta.py stays ?-dialect for the SQLite catalogues)
-- ---------------------------------------------------------------------------

-- :name meta_select
SELECT value FROM _meta WHERE key = %s;

-- :name meta_upsert
INSERT INTO _meta (key, value) VALUES (%s, %s)
ON CONFLICT (key) DO UPDATE SET value = excluded.value;

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

-- zones.id is builder-assigned (plain bigint PK, no identity): upsert_zones
-- resolves each source name to its existing id (or allocates max+1) so a
-- metadata rebuild preserves ids — zone_encounters FKs (curator data) stay
-- attached across rebuilds.
-- :name upsert_zone
INSERT INTO zones (
    id, name, name_lower,
    expansion_short, expansion_name, expansion_year,
    expansion_confidence, expansion_source,
    is_persistent_instance, is_endless_persistent,
    is_tradeskill, is_pvp, is_openworld, is_instance,
    is_live_event, is_city, is_contested, is_deprecated,
    event_name, wiki_url
) VALUES (
    %(id)s, %(name)s, %(name_lower)s,
    %(expansion_short)s, %(expansion_name)s, %(expansion_year)s,
    %(expansion_confidence)s, %(expansion_source)s,
    %(is_persistent_instance)s, %(is_endless_persistent)s,
    %(is_tradeskill)s, %(is_pvp)s, %(is_openworld)s, %(is_instance)s,
    %(is_live_event)s, %(is_city)s, %(is_contested)s, %(is_deprecated)s,
    %(event_name)s, %(wiki_url)s
)
ON CONFLICT (id) DO UPDATE SET
    name                   = excluded.name,
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

-- :name select_all_zone_ids
SELECT id, name FROM zones;

-- Stale-zone prune for the metadata rebuild: zones gone from the cleaned
-- JSON are removed (their types/aliases/encounters cascade). Caller reports
-- the rowcount — a non-zero prune on a routine rebuild deserves a look.
-- :name delete_zones_not_named
DELETE FROM zones WHERE NOT (name = ANY(%s));

-- :name select_zone_id_by_name
SELECT id FROM zones WHERE name = %s;

-- :name select_zone_name_and_expansion
SELECT name, expansion_short FROM zones WHERE id = %s;

-- :name select_zone_name_by_id
SELECT name FROM zones WHERE id = %s;

-- :name find_zone_by_name_lower
SELECT {cols} FROM zones WHERE name_lower = %s LIMIT 1;

-- :name find_zone_by_id
SELECT {cols} FROM zones WHERE id = %s;

-- :name find_zone_id_by_alias
SELECT zone_id FROM zone_aliases WHERE alias_lower = %s LIMIT 1;

-- :name find_zone_id_by_alias_aliased
SELECT zone_id AS id FROM zone_aliases WHERE alias_lower = %s;

-- :name select_zone_id_by_name_lower
SELECT id FROM zones WHERE name_lower = %s;

-- :name list_zones_by_expansion
SELECT {cols} FROM zones WHERE expansion_short = %s ORDER BY name;

-- :name list_zones_by_expansion_typed
SELECT {cols} FROM zones
WHERE expansion_short = %s
  AND id IN (SELECT zone_id FROM zone_types WHERE type = %s)
ORDER BY name;

-- :name list_zones_by_event
SELECT {cols} FROM zones WHERE is_live_event = 1 AND event_name = %s ORDER BY name;

-- :name list_zones_by_type
SELECT {cols} FROM zones
WHERE id IN (SELECT zone_id FROM zone_types WHERE type = %s)
ORDER BY name;

-- :name list_zones_by_boss
SELECT {cols} FROM zones
WHERE id IN (
    SELECT e.zone_id FROM zone_encounters e
    INNER JOIN zone_encounter_mobs m ON m.encounter_id = e.id
    WHERE m.mob_name_lower = %s
)
ORDER BY name;

-- NULLS LAST keeps SQLite's DESC ordering (unknown-year rows sort to the
-- bottom; Postgres DESC would put them first).
-- :name list_distinct_expansions
SELECT DISTINCT expansion_short, expansion_name, expansion_year
FROM zones
WHERE expansion_short IS NOT NULL
ORDER BY expansion_year DESC NULLS LAST;

-- :name expansion_counts
SELECT expansion_short, COUNT(*) AS n FROM zones GROUP BY expansion_short ORDER BY 2 DESC;

-- :name check_zone_exists_for_expansion
SELECT 1 FROM zones WHERE expansion_short = %s LIMIT 1;

-- :name select_expansion_name_year
SELECT expansion_name AS name, expansion_year AS year FROM zones
WHERE expansion_short = %s LIMIT 1;

-- ---------------------------------------------------------------------------
-- zone_types CRUD
-- ---------------------------------------------------------------------------

-- :name delete_zone_types_for_zone
DELETE FROM zone_types WHERE zone_id = %s;

-- :name insert_zone_type
INSERT INTO zone_types (zone_id, type) VALUES (%s, %s);

-- :name insert_zone_type_or_ignore
INSERT INTO zone_types (zone_id, type) VALUES (%s, %s)
ON CONFLICT DO NOTHING;

-- :name delete_zone_type
DELETE FROM zone_types WHERE zone_id = %s AND type = %s;

-- :name list_types_for_zone
SELECT type FROM zone_types WHERE zone_id = %s ORDER BY type;

-- :name check_zone_is_raid
SELECT 1 FROM zone_types WHERE zone_id = %s AND type IN ('raid_x4', 'raid_x2') LIMIT 1;

-- ---------------------------------------------------------------------------
-- zone_aliases CRUD
-- ---------------------------------------------------------------------------

-- :name delete_zone_aliases_for_zone
DELETE FROM zone_aliases WHERE zone_id = %s;

-- :name insert_zone_alias
INSERT INTO zone_aliases (alias, alias_lower, zone_id) VALUES (%s, %s, %s);

-- :name list_aliases_for_zone
SELECT alias FROM zone_aliases WHERE zone_id = %s ORDER BY alias;

-- ---------------------------------------------------------------------------
-- zone_encounters CRUD
-- ---------------------------------------------------------------------------

-- :name delete_encounters_for_zone
DELETE FROM zone_encounters WHERE zone_id = %s;

-- zone_encounters.id is GENERATED ALWAYS AS IDENTITY — runtime inserts never
-- supply it; new ids come back via RETURNING.
-- :name insert_encounter
INSERT INTO zone_encounters (zone_id, encounter_name, position, stage, wiki_url) VALUES (%s, %s, %s, %s, %s)
RETURNING id;

-- :name list_encounters_for_zone
SELECT id, zone_id, encounter_name, position, stage, wiki_url
FROM zone_encounters WHERE zone_id = %s ORDER BY position;

-- :name select_encounter_by_id
SELECT id, zone_id, encounter_name, position, stage, wiki_url FROM zone_encounters WHERE id = %s;

-- :name select_encounter_zone_and_name
SELECT id, zone_id, encounter_name FROM zone_encounters WHERE id = %s;

-- :name select_encounter_zone_id
SELECT zone_id FROM zone_encounters WHERE id = %s;

-- :name update_encounter_meta
UPDATE zone_encounters SET encounter_name = %s, stage = %s, wiki_url = %s WHERE id = %s;

-- :name update_encounter_name
UPDATE zone_encounters SET encounter_name = %s WHERE id = %s;

-- :name update_encounter_position
UPDATE zone_encounters SET position = %s WHERE id = %s;

-- :name delete_encounter_by_id
DELETE FROM zone_encounters WHERE id = %s;

-- :name list_zone_encounter_positions
SELECT id, encounter_name, position FROM zone_encounters WHERE zone_id = %s;

-- :name max_encounter_position_for_zone
SELECT COALESCE(MAX(position), 0) + 1 AS p FROM zone_encounters WHERE zone_id = %s;

-- ---------------------------------------------------------------------------
-- zone_encounter_mobs CRUD
-- ---------------------------------------------------------------------------

-- :name insert_encounter_mob
INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower, position) VALUES (%s, %s, %s, %s)
RETURNING id;

-- :name insert_encounter_mob_primary
INSERT INTO zone_encounter_mobs (encounter_id, mob_name, mob_name_lower, position) VALUES (%s, %s, %s, 0)
RETURNING id;

-- :name update_encounter_mob_primary_rename
UPDATE zone_encounter_mobs SET mob_name = %s, mob_name_lower = %s
WHERE encounter_id = %s AND position = 0;

-- :name list_mobs_for_encounter
SELECT id, mob_name, position FROM zone_encounter_mobs WHERE encounter_id = %s ORDER BY position;

-- :name list_mobs_for_encounter_asc
SELECT id, mob_name, position FROM zone_encounter_mobs WHERE encounter_id = %s ORDER BY position ASC;

-- :name select_primary_mob_name
SELECT mob_name FROM zone_encounter_mobs WHERE encounter_id = %s AND position = 0;

-- :name select_primary_mob_id_and_name
SELECT id, mob_name FROM zone_encounter_mobs WHERE encounter_id = %s AND position = 0;

-- :name select_mob_by_id
SELECT id, mob_name, position FROM zone_encounter_mobs WHERE id = %s;

-- :name select_mob_for_update
SELECT encounter_id, mob_name, position FROM zone_encounter_mobs WHERE id = %s;

-- :name select_mob_for_promote
SELECT id, encounter_id, mob_name, position FROM zone_encounter_mobs WHERE id = %s;

-- :name select_mob_encounter_position
SELECT id, encounter_id, position FROM zone_encounter_mobs WHERE id = %s;

-- :name update_mob_name
UPDATE zone_encounter_mobs SET mob_name = %s, mob_name_lower = %s WHERE id = %s;

-- :name update_mob_position
UPDATE zone_encounter_mobs SET position = %s WHERE id = %s;

-- :name update_mob_position_to_zero
UPDATE zone_encounter_mobs SET position = 0 WHERE id = %s;

-- :name update_mob_position_to_neg_one
UPDATE zone_encounter_mobs SET position = -1 WHERE id = %s;

-- :name shift_mobs_negative
UPDATE zone_encounter_mobs SET position = -position - 1 WHERE encounter_id = %s;

-- :name shift_mobs_back_positive
UPDATE zone_encounter_mobs SET position = -position WHERE encounter_id = %s;

-- :name max_mob_position_for_encounter
SELECT COALESCE(MAX(position), -1) + 1 AS p FROM zone_encounter_mobs WHERE encounter_id = %s;

-- :name count_mobs_for_encounter
SELECT COUNT(*) AS n FROM zone_encounter_mobs WHERE encounter_id = %s;

-- :name delete_mob_by_id
DELETE FROM zone_encounter_mobs WHERE id = %s;

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
ORDER BY z.expansion_year DESC NULLS LAST, z.expansion_short;

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
ORDER BY z.expansion_year DESC NULLS LAST, z.expansion_short;

-- :name insert_featured_raid_expansion
INSERT INTO featured_raid_expansions (expansion_short) VALUES (%s)
ON CONFLICT DO NOTHING;

-- :name remove_featured_raid_zones_in_expansion
DELETE FROM featured_raid_zones
 WHERE zone_id IN (
     SELECT id FROM zones WHERE expansion_short = %s
 );

-- :name delete_featured_raid_expansion
DELETE FROM featured_raid_expansions WHERE expansion_short = %s;

-- NULLS FIRST keeps the implicit Uncategorised lane (category IS NULL) at the
-- top — SQLite sorted NULL first by default; Postgres ASC default is last.
-- :name list_featured_raid_zones
SELECT {cols},
       f.position AS featured_position,
       f.category AS featured_category
FROM zones z
JOIN featured_raid_zones f ON f.zone_id = z.id
WHERE z.expansion_short = %s
ORDER BY f.category NULLS FIRST, f.position;

-- :name list_available_raid_zones
SELECT DISTINCT {cols}
FROM zones z
JOIN zone_types t ON t.zone_id = z.id
WHERE z.expansion_short = %s
  AND t.type IN ('raid_x4', 'raid_x2')
  AND z.id NOT IN (SELECT zone_id FROM featured_raid_zones)
ORDER BY name;

-- :name max_featured_position_uncategorised
SELECT COALESCE(MAX(f.position), -1) AS p
FROM featured_raid_zones f
JOIN zones z2 ON z2.id = f.zone_id
WHERE z2.expansion_short = %s AND f.category IS NULL;

-- :name insert_featured_raid_zone_uncategorised
INSERT INTO featured_raid_zones (zone_id, position, category) VALUES (%s, %s, NULL)
ON CONFLICT DO NOTHING;

-- :name delete_featured_raid_zone_by_name
DELETE FROM featured_raid_zones
 WHERE zone_id = (SELECT id FROM zones WHERE name_lower = %s LIMIT 1);

-- :name find_featured_zone_id_in_expansion
SELECT z.id FROM zones z
JOIN featured_raid_zones f ON f.zone_id = z.id
WHERE z.name_lower = %s AND z.expansion_short = %s;

-- :name max_category_position
SELECT COALESCE(MAX(position), -1) AS p FROM featured_raid_categories WHERE expansion_short = %s;

-- :name insert_featured_raid_category_or_ignore
INSERT INTO featured_raid_categories (expansion_short, name, position) VALUES (%s, %s, %s)
ON CONFLICT DO NOTHING;

-- :name insert_featured_raid_category
INSERT INTO featured_raid_categories (expansion_short, name, position) VALUES (%s, %s, %s);

-- :name check_featured_raid_category_exists
SELECT 1 FROM featured_raid_categories WHERE expansion_short = %s AND name = %s;

-- :name update_featured_raid_zone_position_and_category
UPDATE featured_raid_zones SET position = %s, category = %s WHERE zone_id = %s;

-- :name update_featured_raid_zone_position
UPDATE featured_raid_zones SET position = %s WHERE zone_id = %s;

-- :name update_featured_raid_category_position
UPDATE featured_raid_categories SET position = %s WHERE expansion_short = %s AND name = %s;

-- :name list_featured_raid_categories
SELECT name, position FROM featured_raid_categories WHERE expansion_short = %s ORDER BY position;

-- :name move_featured_zones_to_null_category
UPDATE featured_raid_zones SET category = NULL
WHERE category = %s
  AND zone_id IN (SELECT id FROM zones WHERE expansion_short = %s);

-- :name delete_featured_raid_category
DELETE FROM featured_raid_categories WHERE expansion_short = %s AND name = %s;
