create schema if not exists census;
set search_path to census, public;

-- census family. Policy: epoch bigints, counters bigint, composite text PKs.
-- data_json is jsonb: psycopg returns it parsed, and the character prefix
-- search reads ->>'cls' in SQL instead of parsing whole blobs in Python.

CREATE TABLE characters (
    name_lower       text    NOT NULL,
    world            text    NOT NULL,
    name             text    NOT NULL,
    level            integer,
    guild_name       text,
    data_json        jsonb   NOT NULL,
    last_resolved_at bigint  NOT NULL,
    updated_at       bigint  NOT NULL,
    PRIMARY KEY (name_lower, world)
);

CREATE TABLE guilds (
    name_lower       text    NOT NULL,
    world            text    NOT NULL,
    name             text    NOT NULL,
    data_json        jsonb   NOT NULL,
    last_resolved_at bigint  NOT NULL,
    updated_at       bigint  NOT NULL,
    PRIMARY KEY (name_lower, world)
);

CREATE TABLE character_aas (
    name_lower         text   NOT NULL,
    world              text   NOT NULL,
    data_json          jsonb  NOT NULL,
    last_resolved_at   bigint NOT NULL,
    PRIMARY KEY (name_lower, world)
);

CREATE TABLE character_gear_sets (
    name_lower         text   NOT NULL,
    world              text   NOT NULL,
    data_json          jsonb  NOT NULL,
    last_resolved_at   bigint NOT NULL,
    PRIMARY KEY (name_lower, world)
);

-- One row per guild per UTC day: the headline numbers of a guild refresh
-- (level / members / accounts / achievements plus two roster reductions).
-- Feeds the guild page History charts. day is 'YYYY-MM-DD' (UTC); the
-- 15-minute refresh overwrites the same day's row so the last capture of
-- the day wins. max_level_members is NULL when the world's registry row
-- (and so its max level) was unknown at capture time.
CREATE TABLE guild_history (
    world              text    NOT NULL,
    name_lower         text    NOT NULL,
    day                text    NOT NULL,
    captured_at        bigint  NOT NULL,
    level              integer,
    members            integer,
    accounts           integer,
    achievement_count  integer,
    max_level_members  integer,
    distinct_classes   integer,
    PRIMARY KEY (world, name_lower, day)
);

-- The store-first name-prefix searches (prefix LIKE on name_lower) need
-- text_pattern_ops to use an index under non-C collations.
CREATE INDEX idx_characters_prefix ON characters (world, name_lower text_pattern_ops);
CREATE INDEX idx_guilds_prefix     ON guilds (world, name_lower text_pattern_ops);
-- The recruiting browse's latest-member-count DISTINCT ON scan.
CREATE INDEX idx_guild_history_latest ON guild_history (world, name_lower, day DESC);

-- seeds
-- (none — census is a runtime mirror; marker kept for the scratch-schema
-- leaser's uniform reset contract)
