create schema if not exists parses;
set search_path to parses, public;

-- parses family. Type policy: identity bigints, blanket bigint for
-- epochs/counters (damage totals exceed int4), double precision for
-- floating-point values, 0/1 flags stay integers, JSON-ish payloads stay
-- text byte-exact. FKs are declared and enforced.

CREATE TABLE encounters (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    world           text    NOT NULL DEFAULT 'Varsoon',
    act_encid       text    NOT NULL,
    title           text    NOT NULL,
    zone            text,
    started_at      bigint  NOT NULL,        -- unix seconds, UTC
    ended_at        bigint  NOT NULL,
    duration_s      bigint  NOT NULL,
    total_damage    bigint  NOT NULL DEFAULT 0,
    encdps          double precision NOT NULL DEFAULT 0,
    kills           bigint  NOT NULL DEFAULT 0,
    deaths          bigint  NOT NULL DEFAULT 0,
    -- ACT's GetEncounterSuccessLevel(): 0=unknown, 1=win, 2=loss, 3=mixed.
    success_level   integer NOT NULL DEFAULT 0,
    source_dsn      text    NOT NULL,
    uploaded_by     text    NOT NULL DEFAULT 'local',
    guild_name      text,
    ingested_at     bigint  NOT NULL,
    -- Soft-delete marker (unix seconds). NULL = visible.
    hidden_at       bigint,
    hidden_by       text,
    -- EQ2Parser client_warnings provenance stamps (JSON text, byte-exact).
    client_warnings text,
    -- Tiered detail retention: set when the cleanup sweep drops this
    -- encounter's attack_types/damage_types rows (curated raid zones 30d,
    -- curated group-instance 14d, any other kept fight 7d).
    -- encounters+combatants live forever; the parse detail
    -- page shows a "breakdown pruned" notice instead of empty tables.
    detail_pruned_at bigint,
    UNIQUE (world, act_encid)
);

CREATE TABLE combatants (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    encounter_id    bigint  NOT NULL REFERENCES encounters(id) ON DELETE CASCADE,
    name            text    NOT NULL,
    ally            integer NOT NULL DEFAULT 0,   -- 0/1 (ACT's 'T'/'F')
    started_at      bigint  NOT NULL DEFAULT 0,
    ended_at        bigint  NOT NULL DEFAULT 0,
    duration_s      bigint  NOT NULL DEFAULT 0,
    damage          bigint  NOT NULL DEFAULT 0,
    damage_perc     double precision NOT NULL DEFAULT 0,
    kills           bigint  NOT NULL DEFAULT 0,
    healed          bigint  NOT NULL DEFAULT 0,
    healed_perc     double precision NOT NULL DEFAULT 0,
    crit_heals      bigint  NOT NULL DEFAULT 0,
    heals           bigint  NOT NULL DEFAULT 0,
    cure_dispels    bigint  NOT NULL DEFAULT 0,
    power_drain     bigint  NOT NULL DEFAULT 0,
    power_replenish bigint  NOT NULL DEFAULT 0,
    dps             double precision NOT NULL DEFAULT 0,
    encdps          double precision NOT NULL DEFAULT 0,
    enchps          double precision NOT NULL DEFAULT 0,
    hits            bigint  NOT NULL DEFAULT 0,
    crit_hits       bigint  NOT NULL DEFAULT 0,
    blocked         bigint  NOT NULL DEFAULT 0,
    misses          bigint  NOT NULL DEFAULT 0,
    swings          bigint  NOT NULL DEFAULT 0,
    heals_taken     bigint  NOT NULL DEFAULT 0,
    damage_taken    bigint  NOT NULL DEFAULT 0,
    deaths          bigint  NOT NULL DEFAULT 0,
    to_hit          double precision NOT NULL DEFAULT 0,
    crit_dam_perc   double precision NOT NULL DEFAULT 0,
    crit_heal_perc  double precision NOT NULL DEFAULT 0,
    crit_types      text,
    threat_str      text,
    threat_delta    bigint  NOT NULL DEFAULT 0,
    -- Identity snapshot frozen at ingest. NULL for pets/NPCs and players
    -- we couldn't resolve at upload time.
    level           integer,
    guild_name      text,
    cls             text,
    ilvl            double precision,
    -- Tri-state classification: NULL = not yet classified, 0/1 = decided.
    is_player       integer,
    UNIQUE (encounter_id, name)
);
-- Churny table + the covering index below needs a clean visibility map for
-- index-only scans — vacuum far more eagerly than the default 20%.
ALTER TABLE combatants SET (autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.02);

CREATE TABLE damage_types (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    combatant_id    bigint  NOT NULL REFERENCES combatants(id) ON DELETE CASCADE,
    grouping_label  text,
    damage_type     text    NOT NULL,
    started_at      bigint  NOT NULL DEFAULT 0,
    ended_at        bigint  NOT NULL DEFAULT 0,
    duration_s      bigint  NOT NULL DEFAULT 0,
    damage          bigint  NOT NULL DEFAULT 0,
    encdps          double precision NOT NULL DEFAULT 0,
    char_dps        double precision NOT NULL DEFAULT 0,
    dps             double precision NOT NULL DEFAULT 0,
    average         double precision NOT NULL DEFAULT 0,
    median          bigint  NOT NULL DEFAULT 0,
    min_hit         bigint  NOT NULL DEFAULT 0,
    max_hit         bigint  NOT NULL DEFAULT 0,
    hits            bigint  NOT NULL DEFAULT 0,
    crit_hits       bigint  NOT NULL DEFAULT 0,
    blocked         bigint  NOT NULL DEFAULT 0,
    misses          bigint  NOT NULL DEFAULT 0,
    swings          bigint  NOT NULL DEFAULT 0,
    to_hit          double precision NOT NULL DEFAULT 0,
    average_delay   double precision NOT NULL DEFAULT 0,
    crit_perc       double precision NOT NULL DEFAULT 0,
    crit_types      text,
    UNIQUE (combatant_id, damage_type)
);

CREATE TABLE attack_types (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    combatant_id    bigint  NOT NULL REFERENCES combatants(id) ON DELETE CASCADE,
    victim          text,
    swing_type      integer NOT NULL DEFAULT 0,
    attack_name     text    NOT NULL,
    started_at      bigint  NOT NULL DEFAULT 0,
    ended_at        bigint  NOT NULL DEFAULT 0,
    duration_s      bigint  NOT NULL DEFAULT 0,
    damage          bigint  NOT NULL DEFAULT 0,
    encdps          double precision NOT NULL DEFAULT 0,
    char_dps        double precision NOT NULL DEFAULT 0,
    dps             double precision NOT NULL DEFAULT 0,
    average         double precision NOT NULL DEFAULT 0,
    median          bigint  NOT NULL DEFAULT 0,
    min_hit         bigint  NOT NULL DEFAULT 0,
    max_hit         bigint  NOT NULL DEFAULT 0,
    resist          text,
    hits            bigint  NOT NULL DEFAULT 0,
    crit_hits       bigint  NOT NULL DEFAULT 0,
    blocked         bigint  NOT NULL DEFAULT 0,
    misses          bigint  NOT NULL DEFAULT 0,
    swings          bigint  NOT NULL DEFAULT 0,
    to_hit          double precision NOT NULL DEFAULT 0,
    average_delay   double precision NOT NULL DEFAULT 0,
    crit_perc       double precision NOT NULL DEFAULT 0,
    crit_types      text,
    UNIQUE (combatant_id, swing_type, attack_name)
);

CREATE TABLE ingest_log (
    world           text   NOT NULL DEFAULT 'Varsoon',
    act_encid       text   NOT NULL,
    encounter_id    bigint NOT NULL REFERENCES encounters(id) ON DELETE CASCADE,
    ingested_at     bigint NOT NULL,
    source_dsn      text   NOT NULL,
    PRIMARY KEY (world, act_encid)
);

CREATE TABLE tamper_reports (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    world                   text    NOT NULL DEFAULT 'Varsoon',
    act_encid               text    NOT NULL,
    title                   text    NOT NULL,
    zone                    text,
    started_at              bigint  NOT NULL,   -- unix seconds, UTC
    ended_at                bigint  NOT NULL,
    duration_s              bigint  NOT NULL,
    total_damage            bigint  NOT NULL DEFAULT 0,
    encdps                  double precision NOT NULL DEFAULT 0,
    reason                  text    NOT NULL,
    reported_at             bigint  NOT NULL,
    uploader_logger_name    text    NOT NULL DEFAULT '',
    uploader_discord_id     text    NOT NULL DEFAULT '',
    uploader_discord_name   text    NOT NULL DEFAULT '',
    guild_name              text,
    -- Byte-exact copy of the rejected payload (forensics) — stays text,
    -- never jsonb (jsonb would normalise key order/whitespace).
    payload_json            text    NOT NULL,
    acknowledged_at         bigint,
    acknowledged_by         text
);

-- Indexes ------------------------------------------------------------------
CREATE INDEX idx_encounters_started_desc  ON encounters (started_at DESC);
CREATE INDEX idx_encounters_zone          ON encounters (zone);
CREATE INDEX idx_encounters_world         ON encounters (world, started_at DESC);
CREATE INDEX idx_encounters_uploaded_by   ON encounters (uploaded_by, started_at DESC);
-- Case-insensitive guild match: the attendance/list reads filter on
-- lower(guild_name) = lower(param).
CREATE INDEX idx_encounters_world_guild_lower ON encounters (world, lower(guild_name), started_at DESC);
-- The tiered-retention sweep's candidate scan (old encounters whose
-- breakdown rows still exist). Partial: rows already pruned drop out.
CREATE INDEX idx_encounters_detail_prune ON encounters (started_at) WHERE detail_pruned_at IS NULL;
CREATE INDEX idx_combatants_encounter     ON combatants (encounter_id);
CREATE INDEX idx_combatants_name          ON combatants (name);
CREATE INDEX idx_combatants_ally          ON combatants (encounter_id, ally);
CREATE INDEX idx_combatants_encounter_is_player ON combatants (encounter_id, is_player);
-- Covering index for the rankings read: key (encounter_id, damage DESC) +
-- INCLUDE payload keeps it index-only with small btree keys.
CREATE INDEX idx_combatants_rankings_cover ON combatants (encounter_id, damage DESC)
    INCLUDE (name, ally, is_player, cls, level, ilvl, guild_name, encdps, enchps, healed, deaths);
CREATE INDEX idx_damage_types_combatant   ON damage_types (combatant_id);
CREATE INDEX idx_attack_types_combatant   ON attack_types (combatant_id);
CREATE INDEX idx_attack_types_damage_desc ON attack_types (combatant_id, damage DESC);
CREATE INDEX idx_tamper_reports_unack ON tamper_reports (reported_at DESC) WHERE acknowledged_at IS NULL;
CREATE INDEX idx_tamper_reports_reporter ON tamper_reports (uploader_discord_id, reported_at DESC);
CREATE INDEX idx_tamper_reports_world_reported ON tamper_reports (world, reported_at DESC);

-- seeds
-- (none — parses is pure runtime data; the marker keeps the scratch-schema
-- leaser's reset contract uniform across family files)
