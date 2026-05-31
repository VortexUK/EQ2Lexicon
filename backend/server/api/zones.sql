-- SQL for backend/server/api/zones.py — zone routes (single zone, list,
-- progress-by-guild).
--
-- `match_encounter_mobs_by_titles_chunk` is parameterised over its IN-clause
-- because SQLite needs a known placeholder count. The caller builds the
-- chunked IN list as ",".join("?" * len(chunk)) and `.format(placeholders=...)`s
-- it in — same template trick used in backend/eq2db/zones.sql for `{cols}`.

-- :name most_recent_parsed_guild
-- Most recent non-null guild_name this user has uploaded a parse for.
SELECT guild_name FROM encounters
WHERE uploaded_by = ? AND guild_name IS NOT NULL AND hidden_at IS NULL
ORDER BY started_at DESC LIMIT 1;

-- :name list_kills_for_guild
-- Every winning row for a guild as (id, title, started_at). Caller filters
-- out NULL titles in Python.
SELECT id, title, started_at FROM encounters
WHERE guild_name = ? AND success_level = 1 AND hidden_at IS NULL;

-- :name match_encounter_mobs_by_titles_chunk
-- For a chunk of mob_lower titles, resolve each to its (zone, encounter)
-- pair. Caller chunks by SQLITE_VAR_CHUNK_SAFE to stay under SQLite's
-- variable limit (default 999) and templates the {placeholders} in.
SELECT m.mob_name_lower AS mob_lower,
       z.name           AS zone_name,
       e.encounter_name AS encounter_name
FROM zone_encounter_mobs m
JOIN zone_encounters     e ON e.id = m.encounter_id
JOIN zones               z ON z.id = e.zone_id
WHERE m.mob_name_lower IN ({placeholders});
