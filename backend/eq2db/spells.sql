-- SQL queries for backend/eq2db/spells.py.
-- DDL (CREATE TABLE / CREATE INDEX) stays in spells.py next to init_db()'s
-- idempotent migration logic. Only DML lives here.

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

-- :name find_by_crc_and_tier
SELECT {cols} FROM spells WHERE crc = ? AND tier = ? LIMIT 1;

-- :name find_by_crc_highest_tier
SELECT {cols} FROM spells WHERE crc = ? ORDER BY tier DESC LIMIT 1;

-- :name find_by_name_exact
SELECT {cols} FROM spells WHERE name_lower = ? ORDER BY level;

-- :name find_by_name_like
SELECT {cols} FROM spells WHERE name_lower LIKE ? ESCAPE '\' ORDER BY level;
