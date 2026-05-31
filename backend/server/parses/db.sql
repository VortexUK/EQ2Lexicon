-- SQL for backend/server/parses/db.py — parses DB schema + DML.
-- Schema is read in init_db; migration rebuilds use a Python .replace() on
-- the schema_* string to swap IF NOT EXISTS off and the table name to the
-- _new sentinel — clean idempotency without a second copy of the CREATE.

-- ---------------------------------------------------------------------------
-- Schema
-- ---------------------------------------------------------------------------

-- :name schema_encounters
CREATE TABLE IF NOT EXISTS encounters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    world           TEXT    NOT NULL DEFAULT 'Varsoon',
    act_encid       TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    zone            TEXT,
    started_at      INTEGER NOT NULL,        -- unix seconds, UTC
    ended_at        INTEGER NOT NULL,
    duration_s      INTEGER NOT NULL,
    total_damage    INTEGER NOT NULL DEFAULT 0,
    encdps          REAL    NOT NULL DEFAULT 0,
    kills           INTEGER NOT NULL DEFAULT 0,
    deaths          INTEGER NOT NULL DEFAULT 0,
    -- ACT's GetEncounterSuccessLevel(): 0=unknown, 1=win, 2=loss, 3=mixed.
    -- Used by /parses to colour the encounter title green/red.
    success_level   INTEGER NOT NULL DEFAULT 0,
    source_dsn      TEXT    NOT NULL,
    uploaded_by     TEXT    NOT NULL DEFAULT 'local',
    guild_name      TEXT,
    ingested_at     INTEGER NOT NULL,
    -- Soft-delete marker (unix seconds). NULL = visible. Set when a boss-kill
    -- parse is "deleted" so the leaderboard entry + its link survive while the
    -- row is hidden from the /parses list. Hard purge removes the row entirely.
    hidden_at       INTEGER,
    UNIQUE (world, act_encid)
);

-- :name schema_combatants
CREATE TABLE IF NOT EXISTS combatants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    encounter_id    INTEGER NOT NULL,
    name            TEXT    NOT NULL,
    ally            INTEGER NOT NULL DEFAULT 0,   -- 0/1 (ACT's 'T'/'F')
    started_at      INTEGER NOT NULL DEFAULT 0,
    ended_at        INTEGER NOT NULL DEFAULT 0,
    duration_s      INTEGER NOT NULL DEFAULT 0,
    damage          INTEGER NOT NULL DEFAULT 0,
    damage_perc     REAL    NOT NULL DEFAULT 0,
    kills           INTEGER NOT NULL DEFAULT 0,
    healed          INTEGER NOT NULL DEFAULT 0,
    healed_perc     REAL    NOT NULL DEFAULT 0,
    crit_heals      INTEGER NOT NULL DEFAULT 0,
    heals           INTEGER NOT NULL DEFAULT 0,
    cure_dispels    INTEGER NOT NULL DEFAULT 0,
    power_drain     INTEGER NOT NULL DEFAULT 0,
    power_replenish INTEGER NOT NULL DEFAULT 0,
    dps             REAL    NOT NULL DEFAULT 0,
    encdps          REAL    NOT NULL DEFAULT 0,
    enchps          REAL    NOT NULL DEFAULT 0,
    hits            INTEGER NOT NULL DEFAULT 0,
    crit_hits       INTEGER NOT NULL DEFAULT 0,
    blocked         INTEGER NOT NULL DEFAULT 0,
    misses          INTEGER NOT NULL DEFAULT 0,
    swings          INTEGER NOT NULL DEFAULT 0,
    heals_taken     INTEGER NOT NULL DEFAULT 0,
    damage_taken    INTEGER NOT NULL DEFAULT 0,
    deaths          INTEGER NOT NULL DEFAULT 0,
    to_hit          REAL    NOT NULL DEFAULT 0,
    crit_dam_perc   REAL    NOT NULL DEFAULT 0,
    crit_heal_perc  REAL    NOT NULL DEFAULT 0,
    crit_types      TEXT,
    threat_str      TEXT,
    threat_delta    INTEGER NOT NULL DEFAULT 0,
    -- Identity snapshot frozen at ingest (resolved via character_cache).
    -- NULL for pets/NPCs and players we couldn't resolve at upload time.
    level           INTEGER,
    guild_name      TEXT,
    cls             TEXT,
    ilvl            REAL,
    FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE,
    UNIQUE (encounter_id, name)
);

-- :name schema_damage_types
CREATE TABLE IF NOT EXISTS damage_types (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    combatant_id    INTEGER NOT NULL,
    grouping_label  TEXT,
    damage_type     TEXT    NOT NULL,
    started_at      INTEGER NOT NULL DEFAULT 0,
    ended_at        INTEGER NOT NULL DEFAULT 0,
    duration_s      INTEGER NOT NULL DEFAULT 0,
    damage          INTEGER NOT NULL DEFAULT 0,
    encdps          REAL    NOT NULL DEFAULT 0,
    char_dps        REAL    NOT NULL DEFAULT 0,
    dps             REAL    NOT NULL DEFAULT 0,
    average         REAL    NOT NULL DEFAULT 0,
    median          INTEGER NOT NULL DEFAULT 0,
    min_hit         INTEGER NOT NULL DEFAULT 0,
    max_hit         INTEGER NOT NULL DEFAULT 0,
    hits            INTEGER NOT NULL DEFAULT 0,
    crit_hits       INTEGER NOT NULL DEFAULT 0,
    blocked         INTEGER NOT NULL DEFAULT 0,
    misses          INTEGER NOT NULL DEFAULT 0,
    swings          INTEGER NOT NULL DEFAULT 0,
    to_hit          REAL    NOT NULL DEFAULT 0,
    average_delay   REAL    NOT NULL DEFAULT 0,
    crit_perc       REAL    NOT NULL DEFAULT 0,
    crit_types      TEXT,
    FOREIGN KEY (combatant_id) REFERENCES combatants(id) ON DELETE CASCADE,
    UNIQUE (combatant_id, damage_type)
);

-- :name schema_attack_types
CREATE TABLE IF NOT EXISTS attack_types (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    combatant_id    INTEGER NOT NULL,
    victim          TEXT,
    swing_type      INTEGER NOT NULL DEFAULT 0,
    attack_name     TEXT    NOT NULL,
    started_at      INTEGER NOT NULL DEFAULT 0,
    ended_at        INTEGER NOT NULL DEFAULT 0,
    duration_s      INTEGER NOT NULL DEFAULT 0,
    damage          INTEGER NOT NULL DEFAULT 0,
    encdps          REAL    NOT NULL DEFAULT 0,
    char_dps        REAL    NOT NULL DEFAULT 0,
    dps             REAL    NOT NULL DEFAULT 0,
    average         REAL    NOT NULL DEFAULT 0,
    median          INTEGER NOT NULL DEFAULT 0,
    min_hit         INTEGER NOT NULL DEFAULT 0,
    max_hit         INTEGER NOT NULL DEFAULT 0,
    resist          TEXT,
    hits            INTEGER NOT NULL DEFAULT 0,
    crit_hits       INTEGER NOT NULL DEFAULT 0,
    blocked         INTEGER NOT NULL DEFAULT 0,
    misses          INTEGER NOT NULL DEFAULT 0,
    swings          INTEGER NOT NULL DEFAULT 0,
    to_hit          REAL    NOT NULL DEFAULT 0,
    average_delay   REAL    NOT NULL DEFAULT 0,
    crit_perc       REAL    NOT NULL DEFAULT 0,
    crit_types      TEXT,
    FOREIGN KEY (combatant_id) REFERENCES combatants(id) ON DELETE CASCADE,
    UNIQUE (combatant_id, swing_type, attack_name)
);

-- :name schema_ingest_log
CREATE TABLE IF NOT EXISTS ingest_log (
    world           TEXT    NOT NULL DEFAULT 'Varsoon',
    act_encid       TEXT    NOT NULL,
    encounter_id    INTEGER NOT NULL,
    ingested_at     INTEGER NOT NULL,
    source_dsn      TEXT    NOT NULL,
    PRIMARY KEY (world, act_encid),
    FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE CASCADE
);

-- :name schema_tamper_reports
CREATE TABLE IF NOT EXISTS tamper_reports (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    world                   TEXT    NOT NULL DEFAULT 'Varsoon',
    act_encid               TEXT    NOT NULL,
    title                   TEXT    NOT NULL,
    zone                    TEXT,
    started_at              INTEGER NOT NULL,   -- unix seconds, UTC
    ended_at                INTEGER NOT NULL,
    duration_s              INTEGER NOT NULL,
    total_damage            INTEGER NOT NULL DEFAULT 0,
    encdps                  REAL    NOT NULL DEFAULT 0,
    reason                  TEXT    NOT NULL,
    reported_at             INTEGER NOT NULL,
    uploader_logger_name    TEXT    NOT NULL DEFAULT '',
    uploader_discord_id     TEXT    NOT NULL DEFAULT '',
    uploader_discord_name   TEXT    NOT NULL DEFAULT '',
    guild_name              TEXT,
    payload_json            TEXT    NOT NULL,
    acknowledged_at         INTEGER,
    acknowledged_by         TEXT
);

-- Multi-statement block — init_db runs via conn.executescript().
-- :name indexes_all
CREATE INDEX IF NOT EXISTS idx_encounters_started_desc  ON encounters (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_encounters_zone          ON encounters (zone);
CREATE INDEX IF NOT EXISTS idx_encounters_world         ON encounters (world, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_encounters_uploaded_by   ON encounters (uploaded_by, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_combatants_encounter     ON combatants (encounter_id);
CREATE INDEX IF NOT EXISTS idx_combatants_name          ON combatants (name);
CREATE INDEX IF NOT EXISTS idx_combatants_ally          ON combatants (encounter_id, ally);
CREATE INDEX IF NOT EXISTS idx_damage_types_combatant   ON damage_types (combatant_id);
CREATE INDEX IF NOT EXISTS idx_attack_types_combatant   ON attack_types (combatant_id);
CREATE INDEX IF NOT EXISTS idx_attack_types_damage_desc ON attack_types (combatant_id, damage DESC);
CREATE INDEX IF NOT EXISTS idx_combatants_encounter_is_player ON combatants (encounter_id, is_player);
CREATE INDEX IF NOT EXISTS idx_tamper_reports_unack ON tamper_reports (reported_at DESC) WHERE acknowledged_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tamper_reports_reporter ON tamper_reports (uploader_discord_id, reported_at DESC);
CREATE INDEX IF NOT EXISTS idx_tamper_reports_world_reported ON tamper_reports (world, reported_at DESC);

-- ---------------------------------------------------------------------------
-- Migration helpers (table rebuilds — used by _migrate_* functions)
-- ---------------------------------------------------------------------------

-- :name migrate_check_attack_types_indexes
SELECT name FROM sqlite_master
WHERE type='index' AND tbl_name='attack_types'
AND name LIKE 'sqlite_autoindex_%';

-- :name migrate_attack_types_insert_into_new
INSERT INTO attack_types_new SELECT * FROM attack_types;

-- :name migrate_attack_types_drop_old
DROP TABLE attack_types;

-- :name migrate_attack_types_rename
ALTER TABLE attack_types_new RENAME TO attack_types;

-- :name migrate_encounters_rename_old
ALTER TABLE encounters RENAME TO encounters_old;

-- :name migrate_encounters_copy_from_old
INSERT INTO encounters (
    id, world, act_encid, title, zone,
    started_at, ended_at, duration_s,
    total_damage, encdps, kills, deaths, success_level,
    source_dsn, uploaded_by, guild_name, ingested_at, hidden_at
)
SELECT
    id, 'Varsoon', act_encid, title, zone,
    started_at, ended_at, duration_s,
    total_damage, encdps, kills, deaths, success_level,
    source_dsn, uploaded_by, guild_name, ingested_at, hidden_at
FROM encounters_old;

-- :name migrate_encounters_drop_old
DROP TABLE encounters_old;

-- :name migrate_ingest_log_rename_old
ALTER TABLE ingest_log RENAME TO ingest_log_old;

-- :name migrate_ingest_log_copy_from_old
INSERT INTO ingest_log (world, act_encid, encounter_id, ingested_at, source_dsn)
SELECT 'Varsoon', act_encid, encounter_id, ingested_at, source_dsn
FROM ingest_log_old;

-- :name migrate_ingest_log_drop_old
DROP TABLE ingest_log_old;

-- ---------------------------------------------------------------------------
-- Insert helpers
-- ---------------------------------------------------------------------------

-- :name insert_encounter
INSERT INTO encounters (
    world, act_encid, title, zone,
    started_at, ended_at, duration_s,
    total_damage, encdps, kills, deaths, success_level,
    source_dsn, uploaded_by, guild_name, ingested_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);

-- :name insert_combatant
INSERT INTO combatants (
    encounter_id, name, ally,
    started_at, ended_at, duration_s,
    damage, damage_perc, kills,
    healed, healed_perc, crit_heals, heals, cure_dispels,
    power_drain, power_replenish,
    dps, encdps, enchps,
    hits, crit_hits, blocked, misses, swings,
    heals_taken, damage_taken, deaths,
    to_hit, crit_dam_perc, crit_heal_perc, crit_types,
    threat_str, threat_delta,
    level, guild_name, cls, ilvl
) VALUES (
    ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?, ?, ?,
    ?, ?,
    ?, ?, ?,
    ?, ?, ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?,
    ?, ?, ?, ?
);

-- :name update_combatant_snapshot
UPDATE combatants SET level = ?, guild_name = ?, cls = ?, ilvl = ?
WHERE encounter_id = ? AND name = ?;

-- :name update_combatant_is_player
UPDATE combatants SET is_player = ? WHERE id = ?;

-- :name invalidate_is_player_cache
UPDATE combatants SET is_player = NULL;

-- :name insert_damage_type
INSERT INTO damage_types (
    combatant_id, grouping_label, damage_type,
    started_at, ended_at, duration_s,
    damage, encdps, char_dps, dps,
    average, median, min_hit, max_hit,
    hits, crit_hits, blocked, misses, swings,
    to_hit, average_delay, crit_perc, crit_types
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);

-- :name insert_attack_type
INSERT INTO attack_types (
    combatant_id, victim, swing_type, attack_name,
    started_at, ended_at, duration_s,
    damage, encdps, char_dps, dps,
    average, median, min_hit, max_hit, resist,
    hits, crit_hits, blocked, misses, swings,
    to_hit, average_delay, crit_perc, crit_types
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);

-- :name mark_ingested
INSERT INTO ingest_log (world, act_encid, encounter_id, ingested_at, source_dsn)
VALUES (?, ?, ?, ?, ?);

-- ---------------------------------------------------------------------------
-- Lookup helpers
-- ---------------------------------------------------------------------------

-- :name check_is_ingested
SELECT 1 FROM ingest_log WHERE world = ? AND act_encid = ? LIMIT 1;

-- :name find_encounter_by_act_encid
SELECT * FROM encounters WHERE world = ? AND act_encid = ? LIMIT 1;

-- :name recent_encounters_by_zone
SELECT * FROM encounters
WHERE world = ? AND zone = ?
ORDER BY started_at DESC
LIMIT ?;

-- :name recent_encounters_all
SELECT * FROM encounters WHERE world = ? ORDER BY started_at DESC LIMIT ?;

-- :name list_encounters_for_admin
-- {where} = "WHERE …" composed in Python; embeds a correlated subquery for
-- the player_count column.
SELECT e.id, e.title, e.zone, e.guild_name, e.uploaded_by, e.started_at,
       e.duration_s, e.success_level, e.hidden_at, e.client_warnings,
       (SELECT COUNT(*) FROM combatants c
          WHERE c.encounter_id = e.id AND c.ally = 1
            AND c.name != '' AND c.name != 'Unknown'
            AND instr(c.name, ' ') = 0) AS player_count
FROM encounters e
{where}
ORDER BY e.started_at DESC
LIMIT ?;

-- :name delete_encounter
DELETE FROM encounters WHERE id = ?;

-- :name soft_delete_encounter
UPDATE encounters SET hidden_at = ? WHERE id = ? AND hidden_at IS NULL;

-- :name unhide_encounter
UPDATE encounters SET hidden_at = NULL WHERE id = ? AND hidden_at IS NOT NULL;

-- :name set_encounter_guild_name
UPDATE encounters SET guild_name = ? WHERE id = ?;

-- :name find_encounters_by_filter
-- {where} = "WHERE …" composed in Python (dynamic guild/world/zone/uploader/date).
SELECT id, title, guild_name, source_dsn FROM encounters {where};

-- :name get_combatants_for_encounter
SELECT * FROM combatants WHERE encounter_id = ? ORDER BY damage DESC;

-- :name get_top_attacks_by_swing_type
-- {placeholders} = comma-joined "?,?,..." for the IN list.
SELECT * FROM attack_types
WHERE combatant_id = ? AND swing_type IN ({placeholders})
ORDER BY damage DESC
LIMIT ?;

-- :name get_top_cures
SELECT * FROM attack_types
WHERE combatant_id = ? AND swing_type IN ({placeholders})
ORDER BY hits DESC, damage DESC
LIMIT ?;

-- :name get_top_threats
SELECT * FROM attack_types
WHERE combatant_id = ?
  AND swing_type IN ({placeholders})
  AND attack_name <> 'All'
ORDER BY damage DESC
LIMIT ?;

-- :name get_damage_types_for_combatant
SELECT * FROM damage_types
WHERE combatant_id = ?
ORDER BY damage DESC;

-- ---------------------------------------------------------------------------
-- client_warnings + tamper_reports
-- ---------------------------------------------------------------------------

-- :name set_encounter_client_warnings
UPDATE encounters SET client_warnings = ? WHERE id = ?;

-- :name insert_tamper_report
INSERT INTO tamper_reports (
    world, act_encid, title, zone,
    started_at, ended_at, duration_s,
    total_damage, encdps,
    reason, reported_at,
    uploader_logger_name, uploader_discord_id, uploader_discord_name,
    guild_name, payload_json
) VALUES (
    ?, ?, ?, ?,
    ?, ?, ?,
    ?, ?,
    ?, ?,
    ?, ?, ?,
    ?, ?
);

-- :name list_tamper_reports
-- {where} composed in Python (filters: world / reason / pending|ack|all)
SELECT id, world, act_encid, title, zone,
       started_at, ended_at, duration_s,
       total_damage, encdps,
       reason, reported_at,
       uploader_logger_name, uploader_discord_id, uploader_discord_name,
       guild_name, payload_json,
       acknowledged_at, acknowledged_by
FROM tamper_reports
{where}
ORDER BY reported_at DESC
LIMIT ?;

-- :name acknowledge_tamper_report
UPDATE tamper_reports
   SET acknowledged_at = ?, acknowledged_by = ?
 WHERE id = ? AND acknowledged_at IS NULL;

-- :name count_pending_tamper_reports
SELECT COUNT(*) FROM tamper_reports WHERE acknowledged_at IS NULL;

-- :name count_pending_tamper_reports_for_world
SELECT COUNT(*) FROM tamper_reports WHERE world = ? AND acknowledged_at IS NULL;
