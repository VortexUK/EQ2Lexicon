-- SQL for backend/eq2db/recipes.py. Schema + DML both live here. The shared
-- `_meta` table is created from backend/eq2db/_meta.sql via _meta.create_table().

-- ---------------------------------------------------------------------------
-- Schema
-- ---------------------------------------------------------------------------

-- :name schema_recipes
CREATE TABLE IF NOT EXISTS recipes (
    -- Identity
    id              INTEGER PRIMARY KEY,
    crc             INTEGER,
    name            TEXT    NOT NULL,
    name_lower      TEXT    NOT NULL,

    -- Classification
    bench           TEXT,       -- crafting station, e.g. "chemistry_table", "forge"
    version         INTEGER,

    -- Primary component (always exactly one)
    primary_comp    TEXT,       -- ingredient display name
    primary_qty     INTEGER,

    -- Secondary components (0 – N) stored as JSON array
    -- [{"description": "Raw Lead", "quantity": 1}, …]
    secondary_comps TEXT    NOT NULL DEFAULT '[]',

    -- Fuel component
    fuel_comp       TEXT,
    fuel_qty        INTEGER,

    -- Output per quality tier: item ID + quantity produced
    out_unfinished_id       INTEGER,
    out_unfinished_count    INTEGER,
    out_simple_id           INTEGER,
    out_simple_count        INTEGER,
    out_worked_id           INTEGER,
    out_worked_count        INTEGER,
    out_elaborate_id        INTEGER,
    out_elaborate_count     INTEGER,
    out_formed_id           INTEGER,
    out_formed_count        INTEGER,

    -- Spell-scroll helpers (NULL for non-spell recipes)
    base_name_lower TEXT,   -- spell name without tier suffix, e.g. "lightning palm iii"
    crafted_tier    TEXT,   -- tier suffix as stored in recipe name, e.g. "Expert"

    -- Metadata
    last_update     INTEGER
);

-- Recipe → tradeskill-class mapping. Many-to-many: a recipe taught by both
-- an Armorer and a Weaponsmith book gets a row for each.
-- :name schema_recipe_classes
CREATE TABLE IF NOT EXISTS recipe_classes (
    recipe_id  INTEGER NOT NULL,
    class      TEXT    NOT NULL,   -- tradeskill class display name, e.g. "Armorer"
    PRIMARY KEY (recipe_id, class)
);

-- Multi-statement block — init_db runs via conn.executescript().
-- :name indexes_recipes
CREATE INDEX IF NOT EXISTS idx_name_lower      ON recipes (name_lower);
CREATE INDEX IF NOT EXISTS idx_bench           ON recipes (bench);
-- recipe_classes: filter by class, and join back to recipes by id
CREATE INDEX IF NOT EXISTS idx_rc_class        ON recipe_classes (class);
CREATE INDEX IF NOT EXISTS idx_rc_recipe       ON recipe_classes (recipe_id);
CREATE INDEX IF NOT EXISTS idx_crc             ON recipes (crc);
-- Reverse-lookup: which recipe produces a given item?
CREATE INDEX IF NOT EXISTS idx_out_formed      ON recipes (out_formed_id);
CREATE INDEX IF NOT EXISTS idx_out_elaborate   ON recipes (out_elaborate_id);
CREATE INDEX IF NOT EXISTS idx_out_simple      ON recipes (out_simple_id);
-- Composite for station + name searches
CREATE INDEX IF NOT EXISTS idx_bench_name      ON recipes (bench, name_lower);
-- Spell-scroll lookup: base name + tier (primary use-case for spellcheck feature)
CREATE INDEX IF NOT EXISTS idx_spell_tier      ON recipes (base_name_lower, crafted_tier);

-- Idempotent migration: add spell-tier columns to pre-existing DBs.
-- _MIGRATIONS in recipes.py drives this — init_db loops it.
-- :name migrate_add_base_name_lower
ALTER TABLE recipes ADD COLUMN base_name_lower TEXT;

-- :name migrate_add_crafted_tier
ALTER TABLE recipes ADD COLUMN crafted_tier    TEXT;

-- :name upsert
INSERT OR REPLACE INTO recipes (
    id, crc, name, name_lower,
    bench, version,
    primary_comp, primary_qty,
    secondary_comps,
    fuel_comp, fuel_qty,
    out_unfinished_id, out_unfinished_count,
    out_simple_id,    out_simple_count,
    out_worked_id,    out_worked_count,
    out_elaborate_id, out_elaborate_count,
    out_formed_id,    out_formed_count,
    base_name_lower, crafted_tier,
    last_update
) VALUES (
    :id, :crc, :name, :name_lower,
    :bench, :version,
    :primary_comp, :primary_qty,
    :secondary_comps,
    :fuel_comp, :fuel_qty,
    :out_unfinished_id, :out_unfinished_count,
    :out_simple_id,     :out_simple_count,
    :out_worked_id,     :out_worked_count,
    :out_elaborate_id,  :out_elaborate_count,
    :out_formed_id,     :out_formed_count,
    :base_name_lower, :crafted_tier,
    :last_update
);

-- :name count
SELECT COUNT(*) FROM recipes;

-- :name select_unbackfilled_tiers
SELECT id, name FROM recipes WHERE crafted_tier IS NULL;

-- :name backfill_tier
UPDATE recipes SET base_name_lower = ?, crafted_tier = ? WHERE id = ?;

-- Column-list fragment shared by every find_* query. Not standalone SQL.
-- :name select_cols
id, crc, name, name_lower, bench, version,
primary_comp, primary_qty, secondary_comps,
fuel_comp, fuel_qty,
out_unfinished_id, out_unfinished_count,
out_simple_id, out_simple_count,
out_worked_id, out_worked_count,
out_elaborate_id, out_elaborate_count,
out_formed_id, out_formed_count,
base_name_lower, crafted_tier,
last_update

-- :name find_by_id
SELECT {cols} FROM recipes WHERE id = ? LIMIT 1;

-- :name find_by_name_exact
SELECT {cols} FROM recipes WHERE name_lower = ? ORDER BY name;

-- :name find_by_name_like
SELECT {cols} FROM recipes WHERE name_lower LIKE ? ESCAPE '\' ORDER BY name;

-- :name find_by_spell
SELECT {cols} FROM recipes
WHERE base_name_lower = ?
  AND crafted_tier    = ?
ORDER BY name;

-- :name find_spells_by_tier
-- {placeholders} = comma-joined "?,?,..." sized at call time.
SELECT {cols} FROM recipes
WHERE base_name_lower IN ({placeholders})
  AND crafted_tier    = ?
ORDER BY name;

-- :name find_by_output_id
SELECT {cols} FROM recipes
WHERE out_formed_id    = :id
   OR out_elaborate_id = :id
   OR out_worked_id    = :id
   OR out_simple_id    = :id
   OR out_unfinished_id = :id
ORDER BY name;
