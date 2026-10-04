create schema if not exists zones;
set search_path to zones, public;

-- Reviewed translation of backend/eq2db/zones.sql's schema blocks.
-- zones.id is BUILDER-ASSIGNED (scripts/build_zones_db.py seeds explicit
-- ids from the cleaned wiki JSON) — plain bigint PK, no identity.
-- zone_encounters / zone_encounter_mobs are curator-managed at runtime —
-- identity ids (the copy script preserves existing ids via OVERRIDING +
-- setval). 0/1 flags stay integers per the type policy.

CREATE TABLE _meta (
    key   text PRIMARY KEY,
    value text
);

CREATE TABLE zones (
    -- Identity
    id                      bigint  PRIMARY KEY,
    name                    text    NOT NULL UNIQUE,
    name_lower              text    NOT NULL,

    -- Expansion attribution
    expansion_short         text    NOT NULL,    -- 'DoF', 'AoM', 'CoE', ...
    expansion_name          text    NOT NULL,    -- 'Desert of Flames', ...
    expansion_year          integer,
    expansion_confidence    text    NOT NULL,    -- 'category', 'live_update', ...
    expansion_source        text,                -- audit trail / reason

    -- Flags
    is_persistent_instance  integer NOT NULL DEFAULT 0,
    is_endless_persistent   integer NOT NULL DEFAULT 0,
    is_tradeskill           integer NOT NULL DEFAULT 0,
    is_pvp                  integer NOT NULL DEFAULT 0,
    is_openworld            integer NOT NULL DEFAULT 0,
    is_instance             integer NOT NULL DEFAULT 0,
    is_live_event           integer NOT NULL DEFAULT 0,
    is_city                 integer NOT NULL DEFAULT 0,
    is_contested            integer NOT NULL DEFAULT 0,
    is_deprecated           integer NOT NULL DEFAULT 0,

    -- Optional metadata
    event_name              text,                -- when is_live_event=1
    wiki_url                text
);

-- Many-to-many zone ↔ type. A zone with both Solo and Group variants gets
-- two rows so a "all group zones in RoK" query is one indexed JOIN.
CREATE TABLE zone_types (
    zone_id  bigint NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    type     text   NOT NULL,
    PRIMARY KEY (zone_id, type)
);

-- Alias → canonical zone. ACT logs may emit "The Fabled Deathtoll" or
-- "Fabled Deathtoll"; find_by_name checks aliases before failing.
CREATE TABLE zone_aliases (
    alias        text   NOT NULL PRIMARY KEY,
    alias_lower  text   NOT NULL,
    zone_id      bigint NOT NULL REFERENCES zones(id) ON DELETE CASCADE
);

-- Raid encounters per zone — hand-curated. Each row is a named encounter
-- (1 mob solo, or 2-4 mobs grouped). encounter_name is the display label;
-- individual mob names live in zone_encounter_mobs for reverse lookup.
CREATE TABLE zone_encounters (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    zone_id         bigint  NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
    encounter_name  text    NOT NULL,
    position        integer NOT NULL,
    stage           text,
    wiki_url        text,
    UNIQUE (zone_id, position)
);

CREATE TABLE zone_encounter_mobs (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    encounter_id    bigint  NOT NULL REFERENCES zone_encounters(id) ON DELETE CASCADE,
    mob_name        text    NOT NULL,
    mob_name_lower  text    NOT NULL,
    position        integer NOT NULL DEFAULT 0
);

CREATE TABLE featured_raid_expansions (
    expansion_short text PRIMARY KEY,
    added_at bigint NOT NULL DEFAULT floor(extract(epoch from now()))
);

-- position + category were post-launch SQLite ALTERs (drag-reorder lanes on
-- /raids) — folded into the base table here. category NULL = the implicit
-- Uncategorised lane (listing sorts it first via NULLS FIRST).
CREATE TABLE featured_raid_zones (
    zone_id  bigint  PRIMARY KEY REFERENCES zones(id) ON DELETE CASCADE,
    added_at bigint  NOT NULL DEFAULT floor(extract(epoch from now())),
    position integer NOT NULL DEFAULT 0,
    category text
);

CREATE TABLE featured_raid_categories (
    expansion_short text    NOT NULL,
    name            text    NOT NULL,
    position        integer NOT NULL,
    PRIMARY KEY (expansion_short, name)
);

CREATE INDEX idx_zones_name_lower    ON zones (name_lower);
CREATE INDEX idx_zones_expansion     ON zones (expansion_short);
CREATE INDEX idx_zones_event         ON zones (is_live_event, event_name);
CREATE INDEX idx_zones_tradeskill    ON zones (is_tradeskill);
CREATE INDEX idx_zone_types_type     ON zone_types (type);
CREATE INDEX idx_zone_types_zone     ON zone_types (zone_id);
CREATE INDEX idx_zone_aliases_lower  ON zone_aliases (alias_lower);
CREATE INDEX idx_zone_aliases_zone   ON zone_aliases (zone_id);
CREATE INDEX idx_zone_enc_zone       ON zone_encounters (zone_id, position);
CREATE INDEX idx_zone_enc_mobs_enc   ON zone_encounter_mobs (encounter_id, position);
CREATE INDEX idx_zone_enc_mobs_lower ON zone_encounter_mobs (mob_name_lower);

-- seeds
-- (none — zone metadata is loaded by scripts/build_zones_db.py and boss
-- data is curator-managed; marker kept for the leaser's reset contract)
