-- SQL for backend/server/api/rankings.py — leaderboard queries.
--
-- Most queries are fixed shapes. `list_winning_encounters_with_player_count`
-- needs the `player_count` subquery interpolated via .format() because that
-- subquery is shared with parses/list.py (`_PLAYER_COUNT_SQL`) — when that
-- module migrates the subquery moves into its own .sql key and rankings.py
-- can compose the two via load_sql at module-load.

-- ---------------------------------------------------------------------------
-- zones.db — boss-tree builders for the leaderboard sidebar
-- ---------------------------------------------------------------------------

-- :name list_all_zone_encounter_mobs
-- Every (mob_lower, zone, encounter) tuple. Caller normalises mob_lower
-- (collapsing fabled/variant suffixes) and builds the inverted index in
-- Python.
SELECT m.mob_name_lower, z.name, e.encounter_name
FROM zone_encounter_mobs m
JOIN zone_encounters e ON e.id = m.encounter_id
JOIN zones z ON z.id = e.zone_id;

-- :name list_zones_by_type_with_encounters
-- Zones tagged with a given zone-type token that have at least one
-- encounter. Ordered newest-expansion-first within type.
SELECT z.id, z.name, z.expansion_short, z.expansion_name
FROM zones z
JOIN zone_types t ON t.zone_id = z.id
WHERE t.type = ?
  AND z.id IN (SELECT DISTINCT zone_id FROM zone_encounters)
ORDER BY z.expansion_year DESC, z.name;

-- :name list_encounter_names_for_zone
SELECT encounter_name FROM zone_encounters
WHERE zone_id = ? ORDER BY position;

-- ---------------------------------------------------------------------------
-- parses.db — winning-encounters scan + player_count refresh
-- ---------------------------------------------------------------------------

-- :name list_winning_encounters_with_player_count
-- Every world-scoped winning encounter, most-recent-first. The
-- player_count_sql template parameter is the shared subquery from
-- parses/list.py (avoids a separate JOIN per row).
SELECT e.id, e.title, e.zone, e.guild_name, e.uploaded_by,
       e.started_at, e.duration_s, e.success_level,
       ({player_count_sql}) AS player_count
FROM encounters e
WHERE e.success_level = 1 AND e.world = ?
ORDER BY e.started_at DESC;

-- :name count_player_combatants_for_encounter
-- Refresh the player count for one encounter after the lazy backfill
-- classifies its combatants.
SELECT COUNT(*) FROM combatants WHERE encounter_id = ? AND is_player = 1;
