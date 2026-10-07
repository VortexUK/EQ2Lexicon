create schema if not exists recipes;
set search_path to recipes, public;

-- Reviewed translation of backend/eq2db/recipes.sql's schema blocks (Phase 2).
-- recipes.id / crc and the out_*_id item references are CENSUS-ASSIGNED and
-- reach ~4.29e9 (> int4) — bigint, no identity. out_level existed only as a
-- SQLite ALTER migration (the upsert never wrote it; the recipe-levels
-- backfill did) — a real column here, filled by the loader at build time.

CREATE TABLE _meta (
    key   text PRIMARY KEY,
    value text
);

CREATE TABLE recipes (
    -- Identity
    id              bigint PRIMARY KEY,
    crc             bigint,
    name            text   NOT NULL,
    name_lower      text   NOT NULL,

    -- Classification
    bench           text,       -- crafting station, e.g. "chemistry_table", "forge"
    version         integer,

    -- Primary component (always exactly one)
    primary_comp    text,       -- ingredient display name
    primary_qty     integer,

    -- Secondary components (0 – N) stored as a JSON array
    -- [{"description": "Raw Lead", "quantity": 1}, …]
    secondary_comps text   NOT NULL DEFAULT '[]',

    -- Fuel component
    fuel_comp       text,
    fuel_qty        integer,

    -- Output per quality tier: item ID + quantity produced
    out_unfinished_id    bigint,
    out_unfinished_count integer,
    out_simple_id        bigint,
    out_simple_count     integer,
    out_worked_id        bigint,
    out_worked_count     integer,
    out_elaborate_id     bigint,
    out_elaborate_count  integer,
    out_formed_id        bigint,
    out_formed_count     integer,

    -- Spell-scroll helpers (NULL for non-spell recipes)
    base_name_lower text,   -- spell name without tier suffix, e.g. "lightning palm iii"
    crafted_tier    text,   -- tier suffix as stored in recipe name, e.g. "Expert"

    -- Crafted-output level, resolved from the items schema at load time.
    -- Drives the T1–T14 craft tier on the recipe page. NULL = no leveled output.
    out_level       integer,

    -- Metadata
    last_update     bigint
);

-- Recipe → tradeskill-class mapping. Many-to-many: a recipe taught by both
-- an Armorer and a Weaponsmith book gets a row for each.
CREATE TABLE recipe_classes (
    recipe_id  bigint NOT NULL,
    class      text   NOT NULL,   -- tradeskill class display name, e.g. "Armorer"
    PRIMARY KEY (recipe_id, class)
);

CREATE INDEX idx_recipes_name_lower ON recipes (name_lower);
CREATE INDEX idx_recipes_bench      ON recipes (bench);
CREATE INDEX idx_recipes_crc        ON recipes (crc);
-- Reverse-lookup: which recipe produces a given item?
CREATE INDEX idx_recipes_out_formed    ON recipes (out_formed_id);
CREATE INDEX idx_recipes_out_elaborate ON recipes (out_elaborate_id);
CREATE INDEX idx_recipes_out_simple    ON recipes (out_simple_id);
-- Composite for station + name searches
CREATE INDEX idx_recipes_bench_name ON recipes (bench, name_lower);
-- Spell-scroll lookup: base name + tier (spellcheck upgrade-materials path)
CREATE INDEX idx_recipes_spell_tier ON recipes (base_name_lower, crafted_tier);

-- recipe_classes: filter by class, and join back to recipes by id
CREATE INDEX idx_rc_class  ON recipe_classes (class);
CREATE INDEX idx_rc_recipe ON recipe_classes (recipe_id);

-- seeds
-- (none — read-only census-catalogue mirror; marker kept for the
-- scratch-schema leaser's uniform reset contract)
