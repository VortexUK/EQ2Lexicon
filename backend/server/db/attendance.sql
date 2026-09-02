-- SQL for backend/server/db/attendance.py (async aiosqlite).

-- Find the newest session whose window overlaps the incoming snapshot's
-- window widened by the merge gap (params: world, guild, win_end + gap,
-- win_start - gap).
-- :name select_overlapping_session
SELECT id, session_day, seq, started_at, ended_at, zones, scheduled, team_index, uploaders
FROM attendance_sessions
WHERE world = ? AND guild_name = ? AND started_at <= ? AND ended_at >= ?
ORDER BY started_at DESC LIMIT 1;

-- :name select_max_seq
SELECT COALESCE(MAX(seq) + 1, 0) FROM attendance_sessions
WHERE world = ? AND guild_name = ? AND session_day = ?;

-- :name insert_session
INSERT INTO attendance_sessions (world, guild_name, session_day, seq, started_at, ended_at, zones, scheduled, team_index, uploaders)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);

-- :name merge_session_window
UPDATE attendance_sessions
   SET started_at = MIN(started_at, ?), ended_at = MAX(ended_at, ?),
       zones = ?, uploaders = ?, scheduled = MAX(scheduled, ?),
       team_index = COALESCE(team_index, ?),
       updated_at = strftime('%s','now')
 WHERE id = ?;

-- Commutative min/max upsert — uploader arrival order is irrelevant.
-- :name upsert_observation
INSERT INTO attendance_observations (session_id, character_name, kind, first_seen, last_seen)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(session_id, character_name, kind) DO UPDATE SET
    first_seen = MIN(first_seen, excluded.first_seen),
    last_seen  = MAX(last_seen,  excluded.last_seen);

-- :name select_sessions
SELECT id, session_day, seq, started_at, ended_at, zones, scheduled, team_index
FROM attendance_sessions
WHERE world = ? AND guild_name = ? AND (? IS NULL OR id < ?)
ORDER BY id DESC LIMIT ?;

-- :name select_session
SELECT id, world, guild_name, session_day, seq, started_at, ended_at, zones, scheduled, team_index, uploaders
FROM attendance_sessions WHERE id = ?;

-- :name select_observations
SELECT session_id, character_name, kind, first_seen, last_seen
FROM attendance_observations WHERE session_id = ?;

-- :name select_observations_many
-- {placeholders} = comma-joined "?" list composed in Python.
SELECT session_id, character_name, kind, first_seen, last_seen
FROM attendance_observations WHERE session_id IN ({placeholders});

-- users.db connections don't enable PRAGMA foreign_keys (see
-- AsyncStoreBase._db), so the schema's ON DELETE CASCADE never fires —
-- observations are deleted explicitly in the same transaction.
-- :name delete_observations_for_session
DELETE FROM attendance_observations WHERE session_id = ?;

-- :name delete_session
DELETE FROM attendance_sessions WHERE id = ?;
