-- SQL for backend/eq2db/raids.py. Schema + DML both live here. The shared
-- `_meta` table is created from backend/eq2db/_meta.sql via _meta.create_table().

-- ---------------------------------------------------------------------------
-- Schema
-- ---------------------------------------------------------------------------

-- :name schema_raid_zones
CREATE TABLE IF NOT EXISTS raid_zones (
    -- Identity
    id              INTEGER PRIMARY KEY,
    zone_name       TEXT    NOT NULL UNIQUE,   -- matches zones.db zones.name
    zone_name_lower TEXT    NOT NULL,

    -- Denormalised from zones.db (intentional duplication so this DB
    -- is queryable standalone; if the canonical changes, re-run the
    -- scraper / sync job to refresh).
    expansion_short TEXT    NOT NULL,          -- 'Vanilla' / 'DoF' / 'KoS' / 'EoF' / 'RoK'
    wiki_url        TEXT,

    -- Zone-level metadata extracted from the IZoneInformation template
    -- on the wiki. All optional — missing fields just stay NULL.
    access_md       TEXT,                      -- how to get into the zone
    background_md   TEXT,                      -- lore / "Background" wiki section
    overview_md     TEXT,                      -- general zone-level tactics
    level_range     TEXT,                      -- e.g. '72-75'
    zdiff           TEXT,                      -- 'x4' / 'x2' / 'x3'
    lockout_min     TEXT,                      -- e.g. '2 days 20 hours'
    lockout_max     TEXT,                      -- e.g. '7 days'

    -- Audit trail
    source          TEXT    NOT NULL,          -- SOURCE_SCRAPE / SOURCE_MANUAL
    last_synced_at  INTEGER,                   -- unix ts of last wiki re-scrape
    last_edited_at  INTEGER,
    last_edited_by  TEXT                       -- discord_id or 'eq2i_scrape'
);

-- :name schema_raid_encounters
CREATE TABLE IF NOT EXISTS raid_encounters (
    id              INTEGER PRIMARY KEY,
    raid_zone_id    INTEGER NOT NULL REFERENCES raid_zones(id) ON DELETE CASCADE,
    mob_name        TEXT    NOT NULL,
    mob_name_lower  TEXT    NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0,   -- order within the zone

    -- Free-form markdown strategy. Single blob deliberately — PoC
    -- simplicity. If a structured pattern emerges (cures, dispels,
    -- phases) we can split later without breaking callers.
    strategy_md     TEXT,

    wiki_url        TEXT,
    source          TEXT    NOT NULL,
    last_synced_at  INTEGER,
    last_edited_at  INTEGER,
    last_edited_by  TEXT,

    UNIQUE (raid_zone_id, mob_name_lower)
);

-- :name schema_raid_encounter_revisions
CREATE TABLE IF NOT EXISTS raid_encounter_revisions (
    id            INTEGER PRIMARY KEY,
    encounter_id  INTEGER NOT NULL REFERENCES raid_encounters(id) ON DELETE CASCADE,
    edited_at     INTEGER NOT NULL,
    edited_by     TEXT    NOT NULL,              -- discord_id or scrape token
    before_md     TEXT,                          -- previous strategy_md (NULL on create)
    after_md      TEXT NOT NULL,                 -- new strategy_md
    edit_note     TEXT                           -- optional commit-message style note
);

-- :name schema_raid_zone_revisions
CREATE TABLE IF NOT EXISTS raid_zone_revisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    raid_zone_id  INTEGER NOT NULL,
    edited_at     INTEGER NOT NULL,
    edited_by     TEXT    NOT NULL,              -- discord_id or scrape token
    before_md     TEXT,                          -- NULL on the very first row (seed)
    after_md      TEXT    NOT NULL,
    edit_note     TEXT,                          -- optional commit-style note
    FOREIGN KEY (raid_zone_id) REFERENCES raid_zones (id) ON DELETE CASCADE
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
-- :name schema_act_triggers
CREATE TABLE IF NOT EXISTS act_triggers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    raid_encounter_id   INTEGER NOT NULL REFERENCES raid_encounters(id) ON DELETE CASCADE,

    -- Display / curation (web-only — no XML counterpart)
    position            INTEGER NOT NULL DEFAULT 0,    -- ordering within encounter
    label               TEXT,                          -- human-readable summary line; falls back to sound_data/regex preview
    notes               TEXT,                          -- contributor explanation, never exported

    -- ACT <Trigger> attributes (9 fields)
    active              INTEGER NOT NULL DEFAULT 1,
    regex               TEXT    NOT NULL,
    sound_data          TEXT    NOT NULL DEFAULT '',
    sound_type          INTEGER NOT NULL DEFAULT 3,    -- 3 = TTS, 0 = silent / file
    category_restrict   INTEGER NOT NULL DEFAULT 0,
    category            TEXT,                          -- defaults to mob_name at write time
    timer               INTEGER NOT NULL DEFAULT 0,
    timer_name          TEXT,                          -- loose name-FK into act_spell_timers (same encounter)
    tabbed              INTEGER NOT NULL DEFAULT 0,

    -- Audit
    last_edited_at      INTEGER,
    last_edited_by      TEXT,
    created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

-- ACT Spell Timers — named timer definitions referenced by act_triggers
-- via timer_name. One row maps 1:1 to a <Spell> element in ACT's
-- spell_timers.xml. Multiple triggers MAY reference the same timer name
-- within an encounter (DRY); export deduplicates by name.
-- :name schema_act_spell_timers
CREATE TABLE IF NOT EXISTS act_spell_timers (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    raid_encounter_id    INTEGER NOT NULL REFERENCES raid_encounters(id) ON DELETE CASCADE,

    -- Identity (Name is what triggers reference via TimerName)
    name                 TEXT NOT NULL,
    name_lower           TEXT NOT NULL,

    -- ACT <Spell> attributes (17 fields)
    checked              INTEGER NOT NULL DEFAULT 0,
    timer_duration_s     INTEGER NOT NULL,             -- "Timer" attribute in XML
    only_master_ticks    INTEGER NOT NULL DEFAULT 0,
    restrict             INTEGER NOT NULL DEFAULT 0,
    absolute_            INTEGER NOT NULL DEFAULT 0,   -- "Absolute" — column name disambiguated from SQL keyword
    start_wav            TEXT    NOT NULL DEFAULT '',
    warning_wav          TEXT    NOT NULL DEFAULT '',
    warning_value        INTEGER NOT NULL DEFAULT 10,
    radial_display       INTEGER NOT NULL DEFAULT 0,
    modable              INTEGER NOT NULL DEFAULT 0,
    tooltip              TEXT    NOT NULL DEFAULT '',
    fill_color           INTEGER NOT NULL DEFAULT -16776961,  -- ACT default blue (.NET ARGB packed int)
    panel1               INTEGER NOT NULL DEFAULT 1,
    panel2               INTEGER NOT NULL DEFAULT 0,
    remove_value         INTEGER NOT NULL DEFAULT -15,
    category             TEXT,                          -- defaults to mob_name at write time
    restrict_category    INTEGER NOT NULL DEFAULT 0,

    -- Audit
    last_edited_at       INTEGER,
    last_edited_by       TEXT,
    created_at           INTEGER NOT NULL DEFAULT (strftime('%s','now')),

    UNIQUE (raid_encounter_id, name_lower)
);

-- Multi-statement block — init_db runs via conn.executescript().
-- :name indexes_all
CREATE INDEX IF NOT EXISTS idx_raid_zones_name_lower  ON raid_zones (zone_name_lower);
CREATE INDEX IF NOT EXISTS idx_raid_zones_expansion   ON raid_zones (expansion_short);
CREATE INDEX IF NOT EXISTS idx_raid_enc_zone          ON raid_encounters (raid_zone_id, position);
CREATE INDEX IF NOT EXISTS idx_raid_enc_mob_lower     ON raid_encounters (mob_name_lower);
CREATE INDEX IF NOT EXISTS idx_raid_rev_encounter     ON raid_encounter_revisions (encounter_id, edited_at);
CREATE INDEX IF NOT EXISTS idx_raid_zone_rev_zone     ON raid_zone_revisions (raid_zone_id, edited_at);
CREATE INDEX IF NOT EXISTS idx_act_triggers_enc       ON act_triggers (raid_encounter_id, position);
CREATE INDEX IF NOT EXISTS idx_act_triggers_timer     ON act_triggers (raid_encounter_id, timer_name);
CREATE INDEX IF NOT EXISTS idx_act_spell_timers_enc   ON act_spell_timers (raid_encounter_id);

-- ---------------------------------------------------------------------------
-- raid_zones
-- ---------------------------------------------------------------------------

-- :name select_zone_by_name
SELECT id, source FROM raid_zones WHERE zone_name = ?;

-- :name select_zone_id_by_name
SELECT id FROM raid_zones WHERE zone_name = ?;

-- Refresh wiki-owned columns on an existing human-edited row.
-- :name update_zone_wiki_fields
UPDATE raid_zones SET
    expansion_short = ?,
    wiki_url        = ?,
    level_range     = ?,
    zdiff           = ?,
    lockout_min     = ?,
    lockout_max     = ?,
    last_synced_at  = ?
WHERE id = ?;

-- COALESCE-on-conflict so a None caller param means "don't touch", not
-- "clobber to NULL". See the long comment in raids.py:upsert_raid_zone for
-- the history (curators losing zone overviews on encounter edits).
-- :name upsert_zone
INSERT INTO raid_zones (
    zone_name, zone_name_lower,
    expansion_short, wiki_url,
    access_md, background_md, overview_md,
    level_range, zdiff, lockout_min, lockout_max,
    source, last_synced_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(zone_name) DO UPDATE SET
    expansion_short = COALESCE(excluded.expansion_short, raid_zones.expansion_short),
    wiki_url        = COALESCE(excluded.wiki_url,        raid_zones.wiki_url),
    access_md       = COALESCE(excluded.access_md,       raid_zones.access_md),
    background_md   = COALESCE(excluded.background_md,   raid_zones.background_md),
    overview_md     = COALESCE(excluded.overview_md,     raid_zones.overview_md),
    level_range     = COALESCE(excluded.level_range,     raid_zones.level_range),
    zdiff           = COALESCE(excluded.zdiff,           raid_zones.zdiff),
    lockout_min     = COALESCE(excluded.lockout_min,     raid_zones.lockout_min),
    lockout_max     = COALESCE(excluded.lockout_max,     raid_zones.lockout_max),
    source          = excluded.source,
    last_synced_at  = COALESCE(excluded.last_synced_at,  raid_zones.last_synced_at);

-- ---------------------------------------------------------------------------
-- raid_encounters
-- ---------------------------------------------------------------------------

-- :name select_encounter_by_zone_mob
SELECT id, strategy_md FROM raid_encounters WHERE raid_zone_id = ? AND mob_name_lower = ?;

-- :name select_encounter_source
SELECT source FROM raid_encounters WHERE id = ?;

-- :name insert_encounter
INSERT INTO raid_encounters (
    raid_zone_id, mob_name, mob_name_lower, position,
    strategy_md, wiki_url, source,
    last_synced_at, last_edited_at, last_edited_by
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);

-- Refresh sync timestamp + URL + position on a manually-edited row, leaving
-- strategy_md alone — re-scrape doesn't overwrite curator edits.
-- :name update_encounter_url_position_synced
UPDATE raid_encounters SET wiki_url = ?, position = ?, last_synced_at = ? WHERE id = ?;

-- Big conditional update: only the SCRAPE actor refreshes last_synced_at;
-- only non-SCRAPE actors stamp last_edited_at + last_edited_by. The CASE
-- WHEN guards keep the wrong column from being clobbered.
-- :name update_encounter
UPDATE raid_encounters SET
    mob_name        = ?,
    position        = ?,
    strategy_md     = COALESCE(?, strategy_md),
    wiki_url        = ?,
    source          = ?,
    last_synced_at  = CASE WHEN ?=? THEN ? ELSE last_synced_at END,
    last_edited_at  = CASE WHEN ?<>? THEN ? ELSE last_edited_at END,
    last_edited_by  = CASE WHEN ?<>? THEN ? ELSE last_edited_by END
WHERE id = ?;

-- :name rename_encounter_by_zone_mob
UPDATE raid_encounters
   SET mob_name = ?,
       mob_name_lower = ?,
       last_edited_at = strftime('%s','now')
 WHERE id IN (
     SELECT re.id FROM raid_encounters re
     JOIN raid_zones rz ON rz.id = re.raid_zone_id
     WHERE rz.zone_name_lower = ?
       AND re.mob_name_lower = ?
 );

-- :name update_encounter_position_by_zone_mob
UPDATE raid_encounters
   SET position = ?,
       last_edited_at = strftime('%s','now')
 WHERE id IN (
     SELECT re.id FROM raid_encounters re
     JOIN raid_zones rz ON rz.id = re.raid_zone_id
     WHERE rz.zone_name_lower = ?
       AND re.mob_name_lower = ?
 );

-- :name delete_encounter_by_zone_mob
DELETE FROM raid_encounters
 WHERE id IN (
     SELECT re.id FROM raid_encounters re
     JOIN raid_zones rz ON rz.id = re.raid_zone_id
     WHERE rz.zone_name_lower = ?
       AND re.mob_name_lower = ?
 );

-- ---------------------------------------------------------------------------
-- raid_encounter_revisions
-- ---------------------------------------------------------------------------

-- before_md may be NULL (first-ever revision) — caller passes None and SQLite
-- handles the binding. One INSERT covers both seeding and updates.
-- :name insert_encounter_revision
INSERT INTO raid_encounter_revisions
(encounter_id, edited_at, edited_by, before_md, after_md, edit_note)
VALUES (?, ?, ?, ?, ?, ?);

-- ---------------------------------------------------------------------------
-- Read helpers
-- ---------------------------------------------------------------------------

-- :name list_encounter_revisions
SELECT id, encounter_id, edited_at, edited_by, before_md, after_md, edit_note
FROM raid_encounter_revisions
WHERE encounter_id = ? ORDER BY edited_at DESC, id DESC;

-- :name list_zone_revisions
SELECT id, edited_at, edited_by, before_md, after_md, edit_note
FROM raid_zone_revisions WHERE raid_zone_id = ?
ORDER BY edited_at DESC, id DESC;
