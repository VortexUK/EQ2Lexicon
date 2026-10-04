-- SQL for backend/server/api/zones.py — zone routes (single zone, list,
-- progress-by-guild). Both the parses and zones queries run against
-- Postgres (%s placeholders), each on its own schema-scoped connection.

-- :name most_recent_parsed_guild
-- Most recent non-null guild_name this user has uploaded a parse for.
-- Runs against the Postgres parses schema (%s placeholders).
SELECT guild_name FROM encounters
WHERE uploaded_by = %s AND guild_name IS NOT NULL AND hidden_at IS NULL
ORDER BY started_at DESC LIMIT 1;

-- :name list_kills_for_guild
-- Every winning row for a guild as (id, title, started_at). Caller filters
-- out NULL titles in Python. Postgres parses schema (%s placeholders).
SELECT id, title, started_at FROM encounters
WHERE guild_name = %s AND success_level = 1 AND hidden_at IS NULL;

-- :name match_encounter_mobs_by_titles
-- For a set of mob_lower titles (bound as one text[] parameter), resolve
-- each to its (zone, encounter) pair. Zones schema.
SELECT m.mob_name_lower AS mob_lower,
       z.name           AS zone_name,
       e.encounter_name AS encounter_name
FROM zone_encounter_mobs m
JOIN zone_encounters     e ON e.id = m.encounter_id
JOIN zones               z ON z.id = e.zone_id
WHERE m.mob_name_lower = ANY(%s);
