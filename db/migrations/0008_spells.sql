create schema if not exists spells;
set search_path to spells, public;

-- Reviewed translation of backend/eq2db/spells.sql's schema blocks (Phase 2).
-- spells.id and crc are CENSUS-ASSIGNED and reach ~4.29e9 (> int4) — bigint,
-- no identity. SQLite REAL timings become double precision. `effects`
-- existed as a SQLite ALTER migration — a real column here.

CREATE TABLE _meta (
    key   text PRIMARY KEY,
    value text
);

CREATE TABLE spells (
    -- Identity
    id              bigint PRIMARY KEY,
    name            text   NOT NULL,
    name_lower      text   NOT NULL,

    -- Pre-computed base name (Roman-numeral suffix stripped)
    base_name       text   NOT NULL,
    base_name_lower text   NOT NULL,

    -- Classification
    tier            integer,  -- numeric tier id (1=Novice, 2=Apprentice, 5=Adept …)
    tier_name       text,     -- "Apprentice", "Adept", "Master", "Grandmaster" …
    type            text,     -- "spells", "arts", "pcinnates", "tradeskill" …
    typeid          integer,
    level           integer,  -- minimum level to use
    given_by        text,     -- "any", "class", "alternateadvancement" …
    crc             bigint,   -- base-spell grouping key: all tiers share a CRC
    beneficial      integer,  -- 1 = beneficial, 0 = hostile

    -- Pre-computed spellcheck eligibility:
    --   level > 0 AND type IN ('spells','arts')
    --   AND given_by NOT IN ('alternateadvancement','class')
    passes_spellcheck integer NOT NULL DEFAULT 0,

    -- Timing
    cast_secs       double precision,  -- cast_secs_hundredths / 100
    recast_secs     double precision,
    recovery_secs   double precision,  -- recovery_secs_tenths / 10

    -- Targeting
    target_type     text,              -- "self", "single", "group", "ae" …
    aoe_radius      double precision,
    max_targets     integer,

    -- Display
    description     text,
    icon_id         bigint,
    icon_backdrop   bigint,

    -- Spell effects: JSON array of {description, indentation} objects
    effects         text,

    -- Metadata
    last_update     bigint
);

CREATE INDEX idx_spells_name_lower      ON spells (name_lower);
CREATE INDEX idx_spells_base_name_lower ON spells (base_name_lower);
CREATE INDEX idx_spells_crc             ON spells (crc);
CREATE INDEX idx_spells_type            ON spells (type);
CREATE INDEX idx_spells_given_by        ON spells (given_by);
CREATE INDEX idx_spells_level           ON spells (level);
CREATE INDEX idx_spells_tier_name       ON spells (tier_name);
CREATE INDEX idx_spells_last_update     ON spells (last_update);
-- Composite indexes for common query patterns
CREATE INDEX idx_spells_sc_level        ON spells (passes_spellcheck, level);
CREATE INDEX idx_spells_base_tier       ON spells (base_name_lower, tier);

-- seeds
-- (none — read-only census-catalogue mirror; marker kept for the
-- scratch-schema leaser's uniform reset contract)
