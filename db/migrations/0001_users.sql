create schema if not exists users;
set search_path to users, public;

-- ============================================================================
-- users family. Type policy: identity bigints; unix-epoch bigints (NOT
-- timestamptz — Python arithmetic + the JSON contract use raw ints); 0/1
-- flags stay integers (frontend contract); ISO day strings stay text.
--
-- FK policy: every reference to users(discord_id) is DEFERRABLE INITIALLY
-- IMMEDIATE so the erasure sweep runs as ONE transaction under
-- SET CONSTRAINTS ALL DEFERRED. The FKs are enforced.
-- ============================================================================

CREATE TABLE users (
    discord_id       text PRIMARY KEY,
    discord_name     text NOT NULL,
    discord_username text,
    avatar           text,
    first_seen       bigint NOT NULL DEFAULT floor(extract(epoch from now())),
    last_seen        bigint NOT NULL DEFAULT floor(extract(epoch from now())),
    access_status    text   NOT NULL DEFAULT 'pending'
);

-- No CHECK constraints on the status enums (here and on claims/requests/
-- availability): the routes validate them and the value sets grow as data,
-- not schema.

CREATE TABLE character_claims (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    discord_id      text   NOT NULL REFERENCES users(discord_id) DEFERRABLE INITIALLY IMMEDIATE,
    character_name  text   NOT NULL,
    status          text   NOT NULL DEFAULT 'pending',
    requested_at    bigint NOT NULL DEFAULT floor(extract(epoch from now())),
    reviewed_at     bigint,
    reviewed_by     text,
    note            text,
    world           text   NOT NULL DEFAULT 'Varsoon',
    is_primary      integer NOT NULL DEFAULT 0
);

CREATE INDEX idx_claims_discord ON character_claims (discord_id);
CREATE INDEX idx_claims_status  ON character_claims (status);
CREATE INDEX idx_claims_world   ON character_claims (world);
CREATE INDEX idx_claims_primary ON character_claims (world, is_primary) WHERE is_primary = 1;
-- One APPROVED claim per character per world, enforced by the database and
-- not only by the supersede flow.
CREATE UNIQUE INDEX idx_claims_one_approved
    ON character_claims (world, lower(character_name)) WHERE status = 'approved';

CREATE TABLE item_watch (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    world           text   NOT NULL DEFAULT 'Varsoon',
    guild_name      text   NOT NULL,
    character_name  text   NOT NULL,
    item_id         bigint NOT NULL,   -- census item ids exceed int4
    item_name       text   NOT NULL,
    added_by        text   NOT NULL REFERENCES users(discord_id) DEFERRABLE INITIALLY IMMEDIATE,
    added_by_name   text   NOT NULL,
    added_at        bigint NOT NULL DEFAULT floor(extract(epoch from now())),
    first_seen_at   bigint,            -- first time we saw them wearing it (NULL = never)
    last_seen_at    bigint,            -- most recent check where they had it equipped
    last_checked_at bigint,            -- most recent check (any result)
    UNIQUE (world, guild_name, character_name, item_id)
);

-- Composite because every query is world-scoped.
CREATE INDEX idx_watch_guild ON item_watch (world, guild_name);

-- Persistent, admin-grantable roles (admin itself stays env-driven so a
-- DB wipe can't lock admins out; officer is computed from Census rank).
CREATE TABLE user_roles (
    discord_id  text   NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
    role        text   NOT NULL,
    granted_at  bigint NOT NULL DEFAULT floor(extract(epoch from now())),
    granted_by  text   NOT NULL,           -- discord_id of the granting admin
    PRIMARY KEY (discord_id, role)
);

CREATE INDEX idx_user_roles_role ON user_roles (role);

-- Per-role capability map ("does this user have capability X?" joins
-- user_roles on role). New capability = INSERT rows, not schema.
CREATE TABLE role_permissions (
    role        text NOT NULL,
    capability  text NOT NULL,
    PRIMARY KEY (role, capability)
);

CREATE INDEX idx_role_permissions_capability ON role_permissions (capability);

-- Self-service role requests (claims-queue pattern: pending / approved /
-- rejected / withdrawn; approval also writes user_roles so the immutable
-- request history and the revocable grant stay decoupled).
CREATE TABLE role_requests (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    discord_id   text   NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
    role         text   NOT NULL,
    status       text   NOT NULL DEFAULT 'pending',
    requested_at bigint NOT NULL DEFAULT floor(extract(epoch from now())),
    reviewed_at  bigint,
    reviewed_by  text,          -- admin's discord_id
    user_note    text,          -- "why I want this" note
    admin_note   text           -- admin's response note
);

CREATE INDEX idx_role_requests_status  ON role_requests (status);
CREATE INDEX idx_role_requests_discord ON role_requests (discord_id);
-- Only one pending request per (user, role); resolved ones coexist as audit.
CREATE UNIQUE INDEX idx_role_requests_one_pending
    ON role_requests (discord_id, role) WHERE status = 'pending';

CREATE TABLE api_tokens (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      text   NOT NULL REFERENCES users(discord_id) DEFERRABLE INITIALLY IMMEDIATE,
    name         text   NOT NULL,           -- user-given label e.g. "Desktop ACT"
    token_hash   text   NOT NULL UNIQUE,    -- sha256 hex of the raw token
    token_prefix text   NOT NULL,           -- first 12 chars for UI display
    created_at   bigint NOT NULL DEFAULT floor(extract(epoch from now())),
    last_used_at bigint,                    -- updated on each successful auth
    revoked_at   bigint                     -- non-NULL = inactive
);

CREATE INDEX idx_tokens_user ON api_tokens (user_id);
-- No separate token_hash index: the UNIQUE constraint's index serves lookups.

CREATE TABLE servers (
    world          text PRIMARY KEY,
    subdomain      text NOT NULL UNIQUE,
    display_name   text NOT NULL,
    max_level      integer NOT NULL,
    current_xpac   text,
    launch_dt      text,
    next_xpac      text,
    next_xpac_dt   text,
    -- Stamped by the automatic xpac rollover; the rankings era-lock cutoff.
    current_xpac_started_dt text,
    updated_at     bigint  NOT NULL DEFAULT floor(extract(epoch from now())),
    is_default     integer NOT NULL DEFAULT 0
);

-- Guild raid schedules: up to 4 teams per guild, 4 raids per team.
CREATE TABLE raid_teams (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    world        text    NOT NULL,
    guild_name   text    NOT NULL,
    team_index   integer NOT NULL,   -- 0..3 (display order)
    name         text    NOT NULL,
    primary_tz   text    NOT NULL,   -- IANA tz, e.g. America/New_York
    twitch_login text,               -- normalized channel login
    updated_by   text    NOT NULL REFERENCES users(discord_id) DEFERRABLE INITIALLY IMMEDIATE,
    updated_at   bigint  NOT NULL DEFAULT floor(extract(epoch from now())),
    UNIQUE (world, guild_name, team_index)
);

CREATE INDEX idx_raid_teams_guild ON raid_teams (world, guild_name);

CREATE TABLE raid_slots (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    team_id    bigint  NOT NULL REFERENCES raid_teams(id) ON DELETE CASCADE,
    slot_index integer NOT NULL,     -- 0..3
    -- ISO weekdays, 1=Mon..7=Sun.
    days       integer[] NOT NULL,
    start_min  integer NOT NULL,     -- minutes since midnight in the team tz
    end_min    integer NOT NULL,     -- may cross midnight; span <= 300 (5h)
    label      text,
    UNIQUE (team_id, slot_index)
);

CREATE INDEX idx_raid_slots_team ON raid_slots (team_id);

-- Favourite (bookmark) characters — not ownership. Names are validated +
-- capitalised at the route layer so the plain UNIQUE is case-insensitive
-- in practice.
CREATE TABLE character_favorites (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    discord_id     text   NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
    character_name text   NOT NULL,  -- canonical capitalised EQ2 name
    world          text   NOT NULL,
    created_at     bigint NOT NULL DEFAULT floor(extract(epoch from now())),
    UNIQUE (discord_id, character_name, world)
);

CREATE INDEX idx_favorites_character ON character_favorites (character_name, world);

-- Download counters: one row per (user, slug) so public counts are
-- distinct-downloaders.
CREATE TABLE download_events (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    discord_id text   NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
    slug       text   NOT NULL,
    created_at bigint NOT NULL DEFAULT floor(extract(epoch from now())),
    UNIQUE (discord_id, slug)
);

CREATE INDEX idx_download_events_slug ON download_events (slug);

-- Raid roster: which guild characters raid and in what capacity.
CREATE TABLE raid_roster_roles (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    world          text    NOT NULL,
    guild_name     text    NOT NULL,
    character_name text    NOT NULL,
    role           text    NOT NULL,     -- raider or raid_alt
    updated_at     bigint  NOT NULL DEFAULT floor(extract(epoch from now())),
    updated_by     text,                 -- officer discord id (tombstoned by erasure)
    placeholder    integer NOT NULL DEFAULT 0,  -- 1 = census-hidden character added by hand
    cls            text,                 -- class label for placeholder rows
    UNIQUE (world, guild_name, character_name)
);

CREATE INDEX idx_raid_roles_guild ON raid_roster_roles (world, guild_name);

-- Canonical merged attendance per guild raid night.
-- zones + uploaders are jsonb so erasure can use an exact key-exists test
-- and the snapshot merge can be an atomic `uploaders || excluded.uploaders`.
CREATE TABLE attendance_sessions (
    id          bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    world       text    NOT NULL,
    guild_name  text    NOT NULL,   -- canonical Census casing
    session_day text    NOT NULL,   -- ISO date bucket (6h evening rollover)
    seq         integer NOT NULL DEFAULT 0,
    started_at  bigint  NOT NULL,
    ended_at    bigint  NOT NULL,
    zones       jsonb   NOT NULL DEFAULT '[]',
    scheduled   integer NOT NULL DEFAULT 0,
    team_index  integer,
    uploaders   jsonb   NOT NULL DEFAULT '{}',   -- audit of contributing discord ids
    created_at  bigint  NOT NULL DEFAULT floor(extract(epoch from now())),
    updated_at  bigint  NOT NULL DEFAULT floor(extract(epoch from now())),
    UNIQUE (world, guild_name, session_day, seq)
);

CREATE INDEX idx_att_sessions_guild ON attendance_sessions (world, guild_name, started_at);

CREATE TABLE attendance_observations (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id     bigint NOT NULL REFERENCES attendance_sessions(id) ON DELETE CASCADE,
    character_name text   NOT NULL,   -- canonical capitalised EQ2 name (voice rows carry a discord id)
    kind           text   NOT NULL,   -- raid / online / voice
    first_seen     bigint NOT NULL,
    last_seen      bigint NOT NULL,
    UNIQUE (session_id, character_name, kind)
);

-- Team layouts keyed by team_index (team ids regenerate on schedule saves —
-- the unkeyed reference is deliberate).
CREATE TABLE raid_placements (
    id             bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    world          text    NOT NULL,
    guild_name     text    NOT NULL,
    team_index     integer NOT NULL,   -- 0..3
    character_name text    NOT NULL,
    group_num      integer,            -- 1..4; NULL when benched/sat out
    slot           integer,            -- 0..5 within the group
    sitout         integer NOT NULL DEFAULT 0,
    updated_at     bigint  NOT NULL DEFAULT floor(extract(epoch from now())),
    updated_by     text,
    UNIQUE (world, guild_name, team_index, character_name)
);

CREATE INDEX idx_raid_place_team ON raid_placements (world, guild_name, team_index);

-- Per-user availability calendar; absent row = Available.
CREATE TABLE user_availability (
    discord_id text   NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
    day        text   NOT NULL,   -- ISO date YYYY-MM-DD
    status     text   NOT NULL,   -- tentative or afk
    updated_at bigint NOT NULL DEFAULT 0,   -- newest-wins vs officer char entries
    PRIMARY KEY (discord_id, day)
);

-- Officer-set per-CHARACTER availability (lower-cased names, world-scoped).
CREATE TABLE character_availability (
    world          text   NOT NULL,
    character_name text   NOT NULL,   -- lower-cased
    day            text   NOT NULL,
    status         text   NOT NULL,   -- available / tentative / afk
    set_by         text   NOT NULL,   -- officer discord id
    updated_at     bigint NOT NULL DEFAULT floor(extract(epoch from now())),
    PRIMARY KEY (world, character_name, day)
);

-- Saved AA planner builds. allocations stays text (opaque JSON round-trip;
-- jsonb buys nothing here).
CREATE TABLE aa_plans (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    discord_id     text   NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE,
    world          text   NOT NULL,
    character_name text   NOT NULL,
    name           text   NOT NULL,
    xpac           text,
    allocations    text   NOT NULL DEFAULT '{}',
    share_slug     text   NOT NULL UNIQUE,
    created_at     bigint NOT NULL DEFAULT floor(extract(epoch from now())),
    updated_at     bigint NOT NULL DEFAULT floor(extract(epoch from now()))
);

CREATE INDEX idx_aa_plans_owner ON aa_plans (discord_id, world, character_name);

-- Discord server <-> EQ2 guild registry (bot /lexicon command group).
CREATE TABLE discord_guild_links (
    discord_guild_id  text PRIMARY KEY,   -- Discord snowflake as text
    world             text   NOT NULL,
    guild_name        text   NOT NULL,
    voice_channel_id  text,               -- NULL = voice polling off
    linked_by         text   NOT NULL,    -- discord id of the linker (tombstoned by erasure)
    updated_at        bigint NOT NULL DEFAULT floor(extract(epoch from now())),
    parses_channel_id text,               -- NULL = parse posting off
    parses_posted_at  bigint NOT NULL DEFAULT 0
);

-- Officer corrections to derived attendance categories.
CREATE TABLE attendance_overrides (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id     bigint NOT NULL REFERENCES attendance_sessions(id) ON DELETE CASCADE,
    character_name text   NOT NULL,
    category       text   NOT NULL,   -- present/sat_out/afk/awol/absent
    set_by         text   NOT NULL,
    set_at         bigint NOT NULL DEFAULT floor(extract(epoch from now())),
    UNIQUE (session_id, character_name)
);

-- Officer-authored attendance timelines (timed periods).
CREATE TABLE attendance_segments (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id     bigint NOT NULL REFERENCES attendance_sessions(id) ON DELETE CASCADE,
    character_name text   NOT NULL,
    category       text   NOT NULL,   -- present/sat_out/afk
    started_at     bigint NOT NULL,
    ended_at       bigint NOT NULL,
    set_by         text   NOT NULL,
    set_at         bigint NOT NULL DEFAULT floor(extract(epoch from now()))
);

CREATE INDEX idx_attendance_segments_session ON attendance_segments (session_id);

-- Per-guild feature switches (leader-or-admin editable); absent row = defaults.
CREATE TABLE guild_settings (
    world                      text    NOT NULL,
    guild_name                 text    NOT NULL,
    officers_can_delete_parses integer NOT NULL DEFAULT 1,
    updated_by                 text,
    updated_at                 bigint  NOT NULL DEFAULT floor(extract(epoch from now())),
    PRIMARY KEY (world, guild_name)
);

-- Site-wide key/value settings (admin page).
CREATE TABLE site_settings (
    key        text PRIMARY KEY,
    value      text   NOT NULL,
    updated_by text   NOT NULL,
    updated_at bigint NOT NULL DEFAULT floor(extract(epoch from now()))
);

-- Guild recruitment profiles, keyed by census guild id (rename-proof).
-- The logo rides as bytea so it stays transactional with its attribution
-- columns (and inside the same backups as everything else).
CREATE TABLE guild_recruitment (
    world            text    NOT NULL,
    guild_id         bigint  NOT NULL,   -- census guild id (rename-proof anchor)
    guild_name       text    NOT NULL,   -- current name, verbatim casing (kept fresh)
    recruiting       integer NOT NULL DEFAULT 0,
    description      text    NOT NULL DEFAULT '',
    classes_json     text    NOT NULL DEFAULT '[]',
    tags_json        text    NOT NULL DEFAULT '[]',
    contacts_json    text    NOT NULL DEFAULT '[]',
    discord_url      text,
    updated_by       text,
    updated_at       bigint  NOT NULL DEFAULT floor(extract(epoch from now())),
    logo             bytea,
    logo_media_type  text,
    logo_uploaded_by text,
    logo_uploaded_at bigint,
    PRIMARY KEY (world, guild_id)
);

CREATE UNIQUE INDEX idx_guild_recruitment_name    ON guild_recruitment (world, guild_name);
CREATE INDEX        idx_guild_recruitment_listing ON guild_recruitment (world, recruiting);

-- seeds
-- Idempotent — the test scratch-schema reset re-runs everything below this
-- marker after TRUNCATE.
INSERT INTO role_permissions (role, capability) VALUES ('contributor', 'edit_content')
ON CONFLICT DO NOTHING;
INSERT INTO servers (world, subdomain, display_name, max_level, is_default)
VALUES ('Varsoon', 'varsoon', 'Varsoon', 70, 1), ('Wuoshi', 'wuoshi', 'Wuoshi', 70, 0)
ON CONFLICT DO NOTHING;
-- Exactly one server row must be the default.
UPDATE servers SET is_default = 1
WHERE world = (SELECT MIN(world) FROM servers)
  AND NOT EXISTS (SELECT 1 FROM servers WHERE is_default = 1);
