-- Current DDL shape of every table owned by backend/server/db.
--
-- ONLY contains CREATE TABLE / CREATE INDEX statements that are safe to run
-- on both a fresh DB and an existing DB. Any column-dependent statement
-- (CREATE INDEX on a column added by ALTER) MUST live in migrations.sql,
-- run AFTER the corresponding ADD COLUMN. Adding such a statement here will
-- silently crash on existing DBs.
--
-- The single `all` block is run via conn.executescript() in __init__.init_db.
-- A test (_assertions.py) reads the same block and asserts schema-level
-- invariants.

-- :name all
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    discord_id       TEXT PRIMARY KEY,
    discord_name     TEXT NOT NULL,
    discord_username TEXT,
    avatar           TEXT,
    first_seen       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    last_seen        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    access_status    TEXT    NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS character_claims (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id      TEXT    NOT NULL REFERENCES users(discord_id),
    character_name  TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending',
    requested_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    reviewed_at     INTEGER,
    reviewed_by     TEXT,
    note            TEXT,
    world           TEXT    NOT NULL DEFAULT 'Varsoon'
);

CREATE INDEX IF NOT EXISTS idx_claims_discord ON character_claims(discord_id);
CREATE INDEX IF NOT EXISTS idx_claims_status  ON character_claims(status);
-- NOTE: the index on character_claims(world) is NOT created here. The schema
-- runs via executescript BEFORE the ALTER that adds `world` to a pre-existing
-- table, so creating it here would raise "no such column: world" on an
-- existing DB. It is created in migrations.sql after the ADD COLUMN
-- migration instead.

CREATE TABLE IF NOT EXISTS item_watch (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    world           TEXT    NOT NULL DEFAULT 'Varsoon',
    guild_name      TEXT    NOT NULL,
    character_name  TEXT    NOT NULL,
    item_id         INTEGER NOT NULL,
    item_name       TEXT    NOT NULL,
    added_by        TEXT    NOT NULL REFERENCES users(discord_id),
    added_by_name   TEXT    NOT NULL,
    added_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    first_seen_at   INTEGER,        -- first time we saw them wearing it (NULL = never)
    last_seen_at    INTEGER,        -- most recent check where they had it equipped
    last_checked_at INTEGER,        -- most recent check (any result)
    UNIQUE(world, guild_name, character_name, item_id)
);

CREATE INDEX IF NOT EXISTS idx_watch_guild ON item_watch(guild_name);

-- Persistent, admin-grantable roles.
--
-- The role-source layering for content-edit gates is:
--   admin       — env-driven (ADMIN_DISCORD_IDS). Stays out of this table so
--                 a DB wipe can't lock admins out.
--   contributor — DB-driven via this table (admin-grantable from the UI).
--   officer     — dynamic, computed from Census guild rank at request time;
--                 never persisted here.
--
-- One row per (user, role) pair. Adding a new role to the system is data, not
-- schema — just start inserting rows under a new role name and gate it where
-- appropriate.
--
-- TODO(future): per-role permission system. Today the role → capability
-- mapping is hardcoded inside `require_editor` (and any future require_X).
-- When the codebase grows >1 capability dimensions, layer a `role_permissions`
-- table (role TEXT, capability TEXT) on top of this and have the deps consult
-- it instead of hardcoded role names. Until then YAGNI.
CREATE TABLE IF NOT EXISTS user_roles (
    discord_id  TEXT    NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
    role        TEXT    NOT NULL,
    granted_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    granted_by  TEXT    NOT NULL,           -- discord_id of the granting admin
    PRIMARY KEY (discord_id, role)
);

CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role);

-- Per-role capability map. The route-layer auth gate (`require_capability`
-- in web/auth_deps.py) JOINs user_roles ↔ role_permissions on `role` to
-- answer "does this user have capability X?".
--
-- Admin is the synthetic "all capabilities" branch and never appears here.
-- Officer DOES appear here even though it's not stored in user_roles — the
-- dep dynamically resolves officer status when it sees an ('officer', X)
-- row and the user lacks the capability via DB roles. That keeps adding a
-- new capability for officer a one-row INSERT rather than a code change.
--
-- New capability = INSERT rows here (admins/contributors/officers as
-- appropriate). Re-seeding is idempotent via INSERT OR IGNORE in init_db.
CREATE TABLE IF NOT EXISTS role_permissions (
    role        TEXT NOT NULL,
    capability  TEXT NOT NULL,
    PRIMARY KEY (role, capability)
);

CREATE INDEX IF NOT EXISTS idx_role_permissions_capability
    ON role_permissions(capability);

-- Self-service role requests. Mirrors the character_claims queue pattern:
-- users submit, admin reviews, request transitions through statuses. On
-- approval the route also writes a row into user_roles so the request +
-- the grant are decoupled (an approved request is immutable history; the
-- grant can be independently revoked).
--
-- Status transitions:
--   pending     — submitted by the user, awaiting admin review
--   approved    — admin approved + user_roles row inserted
--   rejected    — admin rejected (admin_note may carry the reason)
--   withdrawn   — user cancelled their own pending request
CREATE TABLE IF NOT EXISTS role_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id   TEXT    NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
    role         TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    requested_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    reviewed_at  INTEGER,
    reviewed_by  TEXT,                                 -- admin's discord_id
    user_note    TEXT,                                 -- "why I want this" note
    admin_note   TEXT                                  -- admin's response note
);

CREATE INDEX IF NOT EXISTS idx_role_requests_status  ON role_requests(status);
CREATE INDEX IF NOT EXISTS idx_role_requests_discord ON role_requests(discord_id);

-- Only one pending request per (user, role) — a second submit while one's
-- in flight is rejected by the route. Resolved requests (approved/rejected/
-- withdrawn) can coexist for the same (user, role) for the audit trail.
CREATE UNIQUE INDEX IF NOT EXISTS idx_role_requests_one_pending
    ON role_requests(discord_id, role) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS api_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT    NOT NULL REFERENCES users(discord_id),
    name            TEXT    NOT NULL,           -- user-given label e.g. "Desktop ACT"
    token_hash      TEXT    NOT NULL UNIQUE,    -- sha256 hex of the raw token
    token_prefix    TEXT    NOT NULL,           -- first 12 chars for UI display (eq2c_ + 7 chars)
    created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    last_used_at    INTEGER,                    -- updated on each successful auth
    revoked_at      INTEGER                     -- non-NULL = inactive
);

CREATE INDEX IF NOT EXISTS idx_tokens_user ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_tokens_hash ON api_tokens(token_hash);

CREATE TABLE IF NOT EXISTS servers (
    world          TEXT PRIMARY KEY,
    subdomain      TEXT NOT NULL UNIQUE,
    display_name   TEXT NOT NULL,
    max_level      INTEGER NOT NULL,
    current_xpac   TEXT,
    launch_dt      TEXT,
    updated_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    is_default     INTEGER NOT NULL DEFAULT 0
);
-- NOTE: no index or statement referencing `is_default` here. SCHEMA runs via
-- executescript BEFORE the ADD COLUMN migration on a pre-existing DB, so any
-- column-dependent DDL/DML must live in migrations.sql after the ALTER, never
-- here.

-- Guild raid schedules. Officer-editable, publicly viewable. Up to 4 teams per
-- guild (team_index 0..3); each team has up to 4 raids (raid_slots, slot_index
-- 0..3). Brand-new tables — CREATE TABLE IF NOT EXISTS is safe on existing DBs,
-- so no migration is needed (unlike an ADD COLUMN).
-- NOTE: inline comments in these column blocks must NOT contain commas —
-- _assertions.py splits column defs on top-level commas to check schema drift.
CREATE TABLE IF NOT EXISTS raid_teams (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    world        TEXT    NOT NULL,
    guild_name   TEXT    NOT NULL,
    team_index   INTEGER NOT NULL,               -- 0..3 (display order)
    name         TEXT    NOT NULL,               -- e.g. Team 1 (officer-editable)
    primary_tz   TEXT    NOT NULL,               -- IANA tz e.g. America/New_York
    twitch_login TEXT,                           -- normalized channel login (nullable)
    updated_by   TEXT    NOT NULL REFERENCES users(discord_id),
    updated_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(world, guild_name, team_index)
);

CREATE INDEX IF NOT EXISTS idx_raid_teams_guild ON raid_teams(world, guild_name);

CREATE TABLE IF NOT EXISTS raid_slots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id    INTEGER NOT NULL REFERENCES raid_teams(id) ON DELETE CASCADE,
    slot_index INTEGER NOT NULL,                 -- 0..3
    days       TEXT    NOT NULL,                 -- CSV of ISO weekdays (1=Mon .. 7=Sun)
    start_min  INTEGER NOT NULL,                 -- minutes since midnight in the team tz (0..1439)
    end_min    INTEGER NOT NULL,                 -- may be < start_min so crosses midnight; span <= 300 (5h)
    label      TEXT,                             -- optional (e.g. Progression)
    UNIQUE(team_id, slot_index)
);

CREATE INDEX IF NOT EXISTS idx_raid_slots_team ON raid_slots(team_id);

-- Favourite (bookmark) characters per user. NOT ownership — carries no guild
-- or claim implications. Scoped per (character, world) so each server's home
-- page shows only that world's favourites. Brand-new table — CREATE TABLE IF
-- NOT EXISTS is safe on existing DBs so no migration is needed.
-- Names are validated + capitalised at the route layer before any DB access
-- so the plain UNIQUE behaves case-insensitively.
CREATE TABLE IF NOT EXISTS character_favorites (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id      TEXT    NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
    character_name  TEXT    NOT NULL,             -- canonical capitalised EQ2 name
    world           TEXT    NOT NULL,
    created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(discord_id, character_name, world)
);

-- Serves the public favourited-by-N count on character pages. The per-user
-- list query is covered by the UNIQUE index prefix (discord_id first).
CREATE INDEX IF NOT EXISTS idx_favorites_character ON character_favorites(character_name, world);

-- ── Raid planning ────────────────────────────────────────────────────────────
-- Officer-curated raid rosters + per-team group layouts + per-user availability.
-- All three are brand-new tables so CREATE TABLE IF NOT EXISTS needs no
-- migration. Names are stored as they appear in the Census roster; lookups
-- lower-case both sides at the query layer.

-- Which guild characters are on the raid roster and in what capacity.
-- Guild-scoped (not team-scoped): a character is a raider for the guild and
-- may then be placed into any team's layout.
CREATE TABLE IF NOT EXISTS raid_roster_roles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    world           TEXT    NOT NULL,
    guild_name      TEXT    NOT NULL,
    character_name  TEXT    NOT NULL,
    role            TEXT    NOT NULL,             -- raider or raid_alt
    updated_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_by      TEXT,                         -- discord id of the officer
    UNIQUE(world, guild_name, character_name)
);

CREATE INDEX IF NOT EXISTS idx_raid_roles_guild ON raid_roster_roles(world, guild_name);

-- Where each rostered character sits in a team's 4x6 layout. Keyed by
-- team_index (position in the guild's raid_teams list) rather than team id:
-- replace_schedule regenerates team rows so ids are not stable across
-- schedule edits. Placements for team indexes beyond the current team count
-- are pruned when the schedule is saved.
CREATE TABLE IF NOT EXISTS raid_placements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    world           TEXT    NOT NULL,
    guild_name      TEXT    NOT NULL,
    team_index      INTEGER NOT NULL,             -- 0..3
    character_name  TEXT    NOT NULL,
    group_num       INTEGER,                      -- 1..4 and NULL when benched or sat out
    slot            INTEGER,                      -- 0..5 within the group
    sitout          INTEGER NOT NULL DEFAULT 0,   -- 1 = parked on the sitout strip
    updated_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_by      TEXT,
    UNIQUE(world, guild_name, team_index, character_name)
);

CREATE INDEX IF NOT EXISTS idx_raid_place_team ON raid_placements(world, guild_name, team_index);

-- Per-user raid availability calendar. Only non-default days are stored —
-- an absent row means Available. Global per user (not per guild or world):
-- a player is AFK on a date regardless of which character they bring.
CREATE TABLE IF NOT EXISTS user_availability (
    discord_id  TEXT NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
    day         TEXT NOT NULL,                    -- ISO date YYYY-MM-DD
    status      TEXT NOT NULL,                    -- tentative or afk
    PRIMARY KEY (discord_id, day)
);
