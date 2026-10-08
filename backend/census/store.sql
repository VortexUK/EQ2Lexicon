-- SQL for backend/census/store.py (psycopg, census schema). DML only —
-- schema DDL lives in db/migrations/0003_census.sql.

-- :name upsert_guild_history
INSERT INTO guild_history (world, name_lower, day, captured_at, level, members, accounts,
                           achievement_count, max_level_members, distinct_classes)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT(world, name_lower, day) DO UPDATE SET
    captured_at=excluded.captured_at, level=excluded.level, members=excluded.members,
    accounts=excluded.accounts, achievement_count=excluded.achievement_count,
    max_level_members=excluded.max_level_members, distinct_classes=excluded.distinct_classes;

-- :name prune_guild_history
DELETE FROM guild_history WHERE world = %s AND name_lower = %s AND day < %s;

-- :name select_guild_history
SELECT day, captured_at, level, members, accounts, achievement_count, max_level_members, distinct_classes
FROM guild_history
WHERE world = %s AND name_lower = %s AND day >= %s
ORDER BY day;

-- Latest-known member count per guild on a world — one row per guild from
-- its most recent history day. DISTINCT ON picks that row;
-- idx_guild_history_latest makes it an ordered index scan.
-- :name select_latest_member_counts
SELECT DISTINCT ON (name_lower) name_lower, members, day
FROM guild_history
WHERE world = %s AND name_lower = ANY(%s)
ORDER BY name_lower, day DESC;

-- :name upsert_character
INSERT INTO characters (name_lower, world, name, level, guild_name, data_json, last_resolved_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT(name_lower, world) DO UPDATE SET
    name=excluded.name, level=excluded.level, guild_name=excluded.guild_name,
    data_json=excluded.data_json, last_resolved_at=excluded.last_resolved_at,
    updated_at=excluded.updated_at;

-- :name select_character
SELECT data_json, last_resolved_at FROM characters WHERE name_lower=%s AND world=%s;

-- :name select_characters_bulk
SELECT name_lower, data_json FROM characters WHERE world=%s AND name_lower = ANY(%s);

-- :name select_character_records_bulk
SELECT name_lower, data_json, last_resolved_at FROM characters WHERE world=%s AND name_lower = ANY(%s);

-- :name upsert_guild
INSERT INTO guilds (name_lower, world, name, data_json, last_resolved_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT(name_lower, world) DO UPDATE SET
    name=excluded.name, data_json=excluded.data_json,
    last_resolved_at=excluded.last_resolved_at, updated_at=excluded.updated_at;

-- :name select_guild
SELECT data_json, last_resolved_at FROM guilds WHERE name_lower=%s AND world=%s;

-- :name select_character_aas
SELECT data_json, last_resolved_at FROM character_aas WHERE name_lower = %s AND world = %s;

-- :name upsert_character_aas
INSERT INTO character_aas (name_lower, world, data_json, last_resolved_at) VALUES (%s, %s, %s, %s)
ON CONFLICT(name_lower, world) DO UPDATE SET data_json = excluded.data_json, last_resolved_at = excluded.last_resolved_at;

-- :name select_character_gear_sets
SELECT data_json, last_resolved_at FROM character_gear_sets WHERE name_lower = %s AND world = %s;

-- :name upsert_character_gear_sets
INSERT INTO character_gear_sets (name_lower, world, data_json, last_resolved_at) VALUES (%s, %s, %s, %s)
ON CONFLICT(name_lower, world) DO UPDATE SET data_json = excluded.data_json, last_resolved_at = excluded.last_resolved_at;

-- Name-prefix search over everything this server has ever seen — the
-- store-first half of /characters/search and /guilds/search (the census
-- round-trip stays off the keystroke path). Callers escape LIKE wildcards.
-- cls is pulled from the jsonb blob in SQL (was a per-row json.loads).
-- :name search_characters_by_prefix
SELECT name, level, guild_name, data_json->>'cls' AS cls FROM characters
WHERE world = %s AND name_lower LIKE %s ESCAPE '\'
ORDER BY name_lower LIMIT %s;

-- :name search_guilds_by_prefix
SELECT name FROM guilds
WHERE world = %s AND name_lower LIKE %s ESCAPE '\'
ORDER BY name_lower LIMIT %s;
