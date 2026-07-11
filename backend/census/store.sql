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

-- ---------------------------------------------------------------------------
-- DML
-- ---------------------------------------------------------------------------

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
INSERT OR REPLACE INTO character_aas (name_lower, world, data_json, last_resolved_at) VALUES (?, ?, ?, ?);
