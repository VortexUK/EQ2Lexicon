create schema if not exists raids;
set search_path to raids, public;

-- Reviewed translation of backend/eq2db/raids.sql's schema blocks. All ids
-- are runtime-generated (scrape ingest + curator edits) — identity columns;
-- the copy script preserves existing ids via OVERRIDING + setval. 0/1 flags
-- stay integers; epoch stamps bigint; markdown/text blobs stay text.

CREATE TABLE _meta (
    key   text PRIMARY KEY,
    value text
);

CREATE TABLE raid_zones (
    -- Identity
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    zone_name       text    NOT NULL UNIQUE,   -- matches zones schema zones.name
    zone_name_lower text    NOT NULL,

    -- Denormalised from the zones family (intentional duplication so this
    -- schema is queryable standalone; if the canonical changes, re-run the
    -- scraper / sync job to refresh).
    expansion_short text    NOT NULL,          -- 'Vanilla' / 'DoF' / 'KoS' / 'EoF' / 'RoK'
    wiki_url        text,

    -- Zone-level metadata extracted from the IZoneInformation template
    -- on the wiki. All optional — missing fields just stay NULL.
    access_md       text,
    background_md   text,
    overview_md     text,
    level_range     text,                      -- e.g. '72-75'
    zdiff           text,                      -- 'x4' / 'x2' / 'x3'
    lockout_min     text,                      -- e.g. '2 days 20 hours'
    lockout_max     text,                      -- e.g. '7 days'

    -- Audit trail
    source          text    NOT NULL,          -- SOURCE_SCRAPE / SOURCE_MANUAL
    last_synced_at  bigint,
    last_edited_at  bigint,
    last_edited_by  text                       -- discord_id or 'eq2i_scrape'
);

CREATE TABLE raid_encounters (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raid_zone_id    bigint  NOT NULL REFERENCES raid_zones(id) ON DELETE CASCADE,
    mob_name        text    NOT NULL,
    mob_name_lower  text    NOT NULL,
    position        integer NOT NULL DEFAULT 0,   -- order within the zone

    -- Free-form markdown strategy. Single blob deliberately — if a
    -- structured pattern emerges (cures, dispels, phases) we can split
    -- later without breaking callers.
    strategy_md     text,

    wiki_url        text,
    source          text    NOT NULL,
    last_synced_at  bigint,
    last_edited_at  bigint,
    last_edited_by  text,

    UNIQUE (raid_zone_id, mob_name_lower)
);

CREATE TABLE raid_encounter_revisions (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    encounter_id  bigint NOT NULL REFERENCES raid_encounters(id) ON DELETE CASCADE,
    edited_at     bigint NOT NULL,
    edited_by     text   NOT NULL,              -- discord_id or scrape token
    before_md     text,                         -- previous strategy_md (NULL on create)
    after_md      text   NOT NULL,              -- new strategy_md
    edit_note     text                          -- optional commit-message style note
);

CREATE TABLE raid_zone_revisions (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raid_zone_id  bigint NOT NULL REFERENCES raid_zones(id) ON DELETE CASCADE,
    edited_at     bigint NOT NULL,
    edited_by     text   NOT NULL,              -- discord_id or scrape token
    before_md     text,                         -- NULL on the very first row (seed)
    after_md      text   NOT NULL,
    edit_note     text                          -- optional commit-style note
);

-- ACT Triggers — regex-driven matchers a player imports into Advanced Combat
-- Tracker to react to in-game log lines (boss callouts, debuffs, mechanic
-- triggers). One row maps 1:1 to a <Trigger> element in ACT's
-- spell_timers.xml export format (column names mirror XML attributes via
-- snake_case).
--
-- A trigger with timer=1 references an entry in act_spell_timers by
-- timer_name; on XML export both rows are emitted so the dropped file
-- round-trips in ACT without manual fix-up.
CREATE TABLE act_triggers (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raid_encounter_id   bigint NOT NULL REFERENCES raid_encounters(id) ON DELETE CASCADE,

    -- Display / curation (web-only — no XML counterpart)
    position            integer NOT NULL DEFAULT 0,
    label               text,
    notes               text,

    -- ACT <Trigger> attributes (9 fields)
    active              integer NOT NULL DEFAULT 1,
    regex               text    NOT NULL,
    sound_data          text    NOT NULL DEFAULT '',
    sound_type          integer NOT NULL DEFAULT 3,    -- 3 = TTS, 0 = silent / file
    category_restrict   integer NOT NULL DEFAULT 0,
    category            text,                          -- defaults to mob_name at write time
    timer               integer NOT NULL DEFAULT 0,
    timer_name          text,                          -- loose name-FK into act_spell_timers (same encounter)
    tabbed              integer NOT NULL DEFAULT 0,

    -- EQ2Parser enrichment (served by /api/act/pack — NEVER exported to
    -- ACT XML; plain ACT stays byte-compatible)
    cooldown_seconds    double precision NOT NULL DEFAULT 1.0,

    -- Audit
    last_edited_at      bigint,
    last_edited_by      text,
    created_at          bigint NOT NULL DEFAULT floor(extract(epoch from now()))
);

-- ACT Spell Timers — named timer definitions referenced by act_triggers
-- via timer_name. One row maps 1:1 to a <Spell> element in ACT's
-- spell_timers.xml. Multiple triggers MAY reference the same timer name
-- within an encounter (DRY); export deduplicates by name.
CREATE TABLE act_spell_timers (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raid_encounter_id    bigint NOT NULL REFERENCES raid_encounters(id) ON DELETE CASCADE,

    -- Identity (Name is what triggers reference via TimerName)
    name                 text NOT NULL,
    name_lower           text NOT NULL,

    -- ACT <Spell> attributes (17 fields)
    checked              integer NOT NULL DEFAULT 0,
    timer_duration_s     integer NOT NULL,             -- "Timer" attribute in XML
    only_master_ticks    integer NOT NULL DEFAULT 0,
    restrict             integer NOT NULL DEFAULT 0,
    absolute_            integer NOT NULL DEFAULT 0,   -- "Absolute" — disambiguated from the SQL keyword
    start_wav            text    NOT NULL DEFAULT '',
    warning_wav          text    NOT NULL DEFAULT '',
    warning_value        integer NOT NULL DEFAULT 10,
    radial_display       integer NOT NULL DEFAULT 0,
    modable              integer NOT NULL DEFAULT 0,
    tooltip              text    NOT NULL DEFAULT '',
    fill_color           integer NOT NULL DEFAULT -16776961,  -- ACT default blue (.NET ARGB packed int)
    panel1               integer NOT NULL DEFAULT 1,
    panel2               integer NOT NULL DEFAULT 0,
    remove_value         integer NOT NULL DEFAULT -15,
    category             text,                          -- defaults to mob_name at write time
    restrict_category    integer NOT NULL DEFAULT 0,

    -- EQ2Parser enrichment (served by /api/act/pack — NEVER exported to
    -- ACT XML; plain ACT stays byte-compatible). Closed vocabularies —
    -- damage_type is comma-joined dominant-first log schools ("poison,
    -- disease"); control_effect is tooltip vocabulary ("stifle", "stun").
    damage_type          text    NOT NULL DEFAULT '',
    control_effect       text    NOT NULL DEFAULT '',

    -- Audit
    last_edited_at       bigint,
    last_edited_by       text,
    created_at           bigint NOT NULL DEFAULT floor(extract(epoch from now())),

    UNIQUE (raid_encounter_id, name_lower)
);

CREATE INDEX idx_raid_zones_name_lower  ON raid_zones (zone_name_lower);
CREATE INDEX idx_raid_zones_expansion   ON raid_zones (expansion_short);
CREATE INDEX idx_raid_enc_zone          ON raid_encounters (raid_zone_id, position);
CREATE INDEX idx_raid_enc_mob_lower     ON raid_encounters (mob_name_lower);
CREATE INDEX idx_raid_rev_encounter     ON raid_encounter_revisions (encounter_id, edited_at);
CREATE INDEX idx_raid_zone_rev_zone     ON raid_zone_revisions (raid_zone_id, edited_at);
CREATE INDEX idx_act_triggers_enc       ON act_triggers (raid_encounter_id, position);
CREATE INDEX idx_act_triggers_timer     ON act_triggers (raid_encounter_id, timer_name);
CREATE INDEX idx_act_spell_timers_enc   ON act_spell_timers (raid_encounter_id);

-- seeds
-- (none — wiki-scrape seeded via scripts/dev/ingest_raids_json.py, then
-- curator-edited in place; marker kept for the leaser's reset contract)
