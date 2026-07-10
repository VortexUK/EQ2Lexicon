-- SQL for backend/eq2db/spells.py. Schema + DML both live here. The shared
-- `_meta` table is created from backend/eq2db/_meta.sql via _meta.create_table().

-- ---------------------------------------------------------------------------
-- Schema
-- ---------------------------------------------------------------------------

-- :name schema_spells
CREATE TABLE IF NOT EXISTS spells (
    -- Identity
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL,
    name_lower      TEXT    NOT NULL,

    -- Pre-computed base name (Roman-numeral suffix stripped)
    base_name       TEXT    NOT NULL,
    base_name_lower TEXT    NOT NULL,

    -- Classification
    tier            INTEGER,            -- numeric tier id (1=Novice, 2=Apprentice, 5=Adept …)
    tier_name       TEXT,               -- "Apprentice", "Adept", "Master", "Grandmaster" …
    type            TEXT,               -- "spells", "arts", "pcinnates", "tradeskill" …
    typeid          INTEGER,
    level           INTEGER,            -- minimum level to use
    given_by        TEXT,               -- "any", "class", "alternateadvancement" …
    crc             INTEGER,            -- base-spell grouping key: all tiers of the same spell share a CRC
    beneficial      INTEGER,            -- 1 = beneficial, 0 = hostile

    -- Pre-computed spellcheck eligibility:
    --   level > 0  AND  type IN ('spells','arts')
    --   AND given_by NOT IN ('alternateadvancement','class')
    passes_spellcheck INTEGER NOT NULL DEFAULT 0,

    -- Timing
    cast_secs       REAL,               -- cast_secs_hundredths / 100
    recast_secs     REAL,
    recovery_secs   REAL,               -- recovery_secs_tenths / 10

    -- Targeting
    target_type     TEXT,               -- "self", "single", "group", "ae" …
    aoe_radius      REAL,
    max_targets     INTEGER,

    -- Display
    description     TEXT,
    icon_id         INTEGER,
    icon_backdrop   INTEGER,

    -- Spell effects: JSON array of {description, indentation} objects
    -- Populated from effect_list[] in the Census /spell/ response.
    effects         TEXT,

    -- Metadata
    last_update     INTEGER
);

-- Multi-statement block — init_db runs via conn.executescript().
-- :name indexes_spells
CREATE INDEX IF NOT EXISTS idx_name_lower       ON spells (name_lower);
CREATE INDEX IF NOT EXISTS idx_base_name_lower  ON spells (base_name_lower);
CREATE INDEX IF NOT EXISTS idx_crc              ON spells (crc);
CREATE INDEX IF NOT EXISTS idx_type             ON spells (type);
CREATE INDEX IF NOT EXISTS idx_given_by         ON spells (given_by);
CREATE INDEX IF NOT EXISTS idx_level            ON spells (level);
CREATE INDEX IF NOT EXISTS idx_tier_name        ON spells (tier_name);
CREATE INDEX IF NOT EXISTS idx_last_update      ON spells (last_update);
-- Composite indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_sc_level         ON spells (passes_spellcheck, level);
CREATE INDEX IF NOT EXISTS idx_base_tier        ON spells (base_name_lower, tier);

-- Idempotent migration: add `effects` column to pre-existing DBs.
-- :name migrate_add_effects_column
ALTER TABLE spells ADD COLUMN effects TEXT;

-- :name upsert
INSERT OR REPLACE INTO spells (
    id, name, name_lower, base_name, base_name_lower,
    tier, tier_name, type, typeid, level, given_by, crc, beneficial,
    passes_spellcheck,
    cast_secs, recast_secs, recovery_secs,
    target_type, aoe_radius, max_targets,
    description, icon_id, icon_backdrop,
    effects,
    last_update
) VALUES (
    :id, :name, :name_lower, :base_name, :base_name_lower,
    :tier, :tier_name, :type, :typeid, :level, :given_by, :crc, :beneficial,
    :passes_spellcheck,
    :cast_secs, :recast_secs, :recovery_secs,
    :target_type, :aoe_radius, :max_targets,
    :description, :icon_id, :icon_backdrop,
    :effects,
    :last_update
);

-- :name count
SELECT COUNT(*) FROM spells;

-- All non-rowid columns — fragment shared by every find_* query. Not a
-- standalone statement; spliced via f-string in Python where needed.
-- :name select_cols
id, name, name_lower, base_name, base_name_lower,
tier, tier_name, type, typeid, level, given_by, crc, beneficial,
passes_spellcheck,
cast_secs, recast_secs, recovery_secs,
target_type, aoe_radius, max_targets,
description, icon_id, icon_backdrop,
effects, last_update

-- :name find_by_id
SELECT {cols} FROM spells WHERE id = ? LIMIT 1;

-- :name find_by_ids
-- {placeholders} is the comma-joined "?,?,?,..." count — sized at call time
-- so SQLite parses the IN list with a fixed parameter count.
SELECT {cols} FROM spells WHERE id IN ({placeholders});

-- :name upgradeable_crcs
-- Of the given CRCs, return those whose spell line has more than one tier
-- (Apprentice → Grandmaster, …) — i.e. genuinely upgradeable spells. Single-tier
-- abilities (Cure, Resurrect, Soothe, …) have one tier_name and are excluded.
-- {placeholders} sized at call time. idx_crc keeps the IN + GROUP BY fast.
SELECT crc FROM spells WHERE crc IN ({placeholders})
GROUP BY crc HAVING COUNT(DISTINCT tier_name) > 1;

-- A (crc, tier) pair has one row PER LEVEL-SCALED VARIANT (level 70 / 100 /
-- 110 / 120 / …). The high-level variants from later expansions carry 0.0%
-- placeholder effect values; the earliest non-zero level (the TLE-era row) is
-- the populated, deployment-relevant one. Without a deterministic ORDER BY,
-- LIMIT 1 returned an arbitrary variant — sometimes a 0.0% placeholder (the
-- "Increases Max Health by 0.0%" AA-tooltip bug). Sort level=0 rows last.
-- :name find_by_crc_and_tier
SELECT {cols} FROM spells WHERE crc = ? AND tier = ?
ORDER BY CASE WHEN level = 0 THEN 9999 ELSE level END ASC LIMIT 1;

-- :name find_by_crc_highest_tier
SELECT {cols} FROM spells WHERE crc = ?
ORDER BY tier DESC, CASE WHEN level = 0 THEN 9999 ELSE level END ASC LIMIT 1;

-- :name find_by_name_exact
SELECT {cols} FROM spells WHERE name_lower = ? ORDER BY level;

-- :name find_by_name_like
SELECT {cols} FROM spells WHERE name_lower LIKE ? ESCAPE '\' ORDER BY level;
