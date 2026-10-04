-- SQL for backend/census/store.py (CensusStore). Schema + DML both live
-- here per the sql_loader convention — one grep target for all SQL.

-- ---------------------------------------------------------------------------
-- Schema
-- ---------------------------------------------------------------------------

-- :name schema_characters
CREATE TABLE IF NOT EXISTS characters (
    name_lower       TEXT    NOT NULL,
    world            TEXT    NOT NULL,
    name             TEXT    NOT NULL,
    level            INTEGER,
    guild_name       TEXT,
    data_json        TEXT    NOT NULL,
    last_resolved_at INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    PRIMARY KEY (name_lower, world)
);

-- :name schema_guilds
CREATE TABLE IF NOT EXISTS guilds (
    name_lower       TEXT    NOT NULL,
    world            TEXT    NOT NULL,
    name             TEXT    NOT NULL,
    data_json        TEXT    NOT NULL,
    last_resolved_at INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    PRIMARY KEY (name_lower, world)
);

-- :name schema_character_aas
CREATE TABLE IF NOT EXISTS character_aas (
    name_lower         TEXT    NOT NULL,
    world              TEXT    NOT NULL,
    data_json          TEXT    NOT NULL,
    last_resolved_at   INTEGER NOT NULL,
    PRIMARY KEY (name_lower, world)
);

-- :name schema_character_gear_sets
CREATE TABLE IF NOT EXISTS character_gear_sets (
    name_lower         TEXT    NOT NULL,
    world              TEXT    NOT NULL,
    data_json          TEXT    NOT NULL,
    last_resolved_at   INTEGER NOT NULL,
    PRIMARY KEY (name_lower, world)
);

-- One row per guild per UTC day: the headline numbers of a guild refresh
-- (level / members / accounts / achievements plus two roster reductions).
-- Feeds the guild page History charts. day is 'YYYY-MM-DD' (UTC); the
-- 15-minute refresh overwrites the same day's row so the last capture of
-- the day wins. max_level_members is NULL when the world's registry row
-- (and so its max level) was unknown at capture time.
-- :name schema_guild_history
CREATE TABLE IF NOT EXISTS guild_history (
    world              TEXT    NOT NULL,
    name_lower         TEXT    NOT NULL,
    day                TEXT    NOT NULL,
    captured_at        INTEGER NOT NULL,
    level              INTEGER,
    members            INTEGER,
    accounts           INTEGER,
    achievement_count  INTEGER,
    max_level_members  INTEGER,
    distinct_classes   INTEGER,
    PRIMARY KEY (world, name_lower, day)
);

-- ---------------------------------------------------------------------------
-- DML
-- ---------------------------------------------------------------------------

-- :name upsert_guild_history
INSERT INTO guild_history (world, name_lower, day, captured_at, level, members, accounts,
                           achievement_count, max_level_members, distinct_classes)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(world, name_lower, day) DO UPDATE SET
    captured_at=excluded.captured_at, level=excluded.level, members=excluded.members,
    accounts=excluded.accounts, achievement_count=excluded.achievement_count,
    max_level_members=excluded.max_level_members, distinct_classes=excluded.distinct_classes;

-- :name prune_guild_history
DELETE FROM guild_history WHERE world = ? AND name_lower = ? AND day < ?;

-- :name select_guild_history
SELECT day, captured_at, level, members, accounts, achievement_count, max_level_members, distinct_classes
FROM guild_history
WHERE world = ? AND name_lower = ? AND day >= ?
ORDER BY day;

-- Latest-known member count per guild on a world — one row per guild from
-- its most recent history day (SQLite's bare-column-with-MAX rule pins
-- members to the MAX(day) row). Feeds the recruiting browse cards.
-- :name select_latest_member_counts
SELECT name_lower, members, MAX(day) AS day
FROM guild_history
WHERE world = ?
GROUP BY name_lower;

-- :name upsert_character
INSERT INTO characters (name_lower, world, name, level, guild_name, data_json, last_resolved_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(name_lower, world) DO UPDATE SET
    name=excluded.name, level=excluded.level, guild_name=excluded.guild_name,
    data_json=excluded.data_json, last_resolved_at=excluded.last_resolved_at,
    updated_at=excluded.updated_at;

-- :name select_character
SELECT data_json, last_resolved_at FROM characters WHERE name_lower=? AND world=?;

-- :name upsert_guild
INSERT INTO guilds (name_lower, world, name, data_json, last_resolved_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(name_lower, world) DO UPDATE SET
    name=excluded.name, data_json=excluded.data_json,
    last_resolved_at=excluded.last_resolved_at, updated_at=excluded.updated_at;

-- :name select_guild
SELECT data_json, last_resolved_at FROM guilds WHERE name_lower=? AND world=?;

-- :name select_character_aas
SELECT data_json, last_resolved_at FROM character_aas WHERE name_lower = ? AND world = ?;

-- :name upsert_character_aas
INSERT INTO character_aas (name_lower, world, data_json, last_resolved_at) VALUES (?, ?, ?, ?)
ON CONFLICT(name_lower, world) DO UPDATE SET data_json = excluded.data_json, last_resolved_at = excluded.last_resolved_at;

-- :name select_character_gear_sets
SELECT data_json, last_resolved_at FROM character_gear_sets WHERE name_lower = ? AND world = ?;

-- :name upsert_character_gear_sets
INSERT INTO character_gear_sets (name_lower, world, data_json, last_resolved_at) VALUES (?, ?, ?, ?)
ON CONFLICT(name_lower, world) DO UPDATE SET data_json = excluded.data_json, last_resolved_at = excluded.last_resolved_at;

-- Name-prefix search over everything this server has ever seen — the
-- store-first half of /characters/search and /guilds/search (the census
-- round-trip stays off the keystroke path). Callers escape LIKE wildcards.
-- :name search_characters_by_prefix
SELECT name, level, guild_name, data_json FROM characters
WHERE world = ? AND name_lower LIKE ? ESCAPE '\'
ORDER BY name_lower LIMIT ?;

-- :name search_guilds_by_prefix
SELECT name FROM guilds
WHERE world = ? AND name_lower LIKE ? ESCAPE '\'
ORDER BY name_lower LIMIT ?;
