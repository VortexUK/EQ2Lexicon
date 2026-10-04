-- SQL for backend/server/db/attendance.py (psycopg, users schema).

-- Find the newest session whose window overlaps the incoming snapshot's
-- window widened by the merge gap (params: world, guild, win_end + gap,
-- win_start - gap).
-- :name select_overlapping_session
SELECT id, session_day, seq, started_at, ended_at, zones, scheduled, team_index, uploaders
FROM attendance_sessions
WHERE world = %s AND guild_name = %s AND started_at <= %s AND ended_at >= %s
ORDER BY started_at DESC LIMIT 1;

-- Point-in-time probe for the voice poller: the session a snapshot at time
-- T WOULD merge into (select_overlapping_session with a zero-width window,
-- widened by the merge gap in Python). Case-insensitive on both keys — the
-- /lexicon registry stores canonical casing but belt-and-braces costs
-- nothing.
-- :name select_live_session
SELECT id, session_day, started_at, ended_at
FROM attendance_sessions
WHERE lower(world) = lower(%s) AND lower(guild_name) = lower(%s)
  AND started_at <= %s AND ended_at >= %s
ORDER BY started_at DESC LIMIT 1;

-- :name select_max_seq
SELECT COALESCE(MAX(seq) + 1, 0) AS seq FROM attendance_sessions
WHERE world = %s AND guild_name = %s AND session_day = %s;

-- :name insert_session
INSERT INTO attendance_sessions (world, guild_name, session_day, seq, started_at, ended_at, zones, scheduled, team_index, uploaders)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id;

-- :name merge_session_window
UPDATE attendance_sessions
   SET started_at = LEAST(started_at, %s), ended_at = GREATEST(ended_at, %s),
       zones = %s, uploaders = %s, scheduled = GREATEST(scheduled, %s),
       team_index = COALESCE(team_index, %s),
       updated_at = floor(extract(epoch from now()))
 WHERE id = %s;

-- Commutative min/max upsert — uploader arrival order is irrelevant.
-- :name upsert_observation
INSERT INTO attendance_observations (session_id, character_name, kind, first_seen, last_seen)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT(session_id, character_name, kind) DO UPDATE SET
    first_seen = LEAST(attendance_observations.first_seen, excluded.first_seen),
    last_seen  = GREATEST(attendance_observations.last_seen,  excluded.last_seen);

-- The before_id probe parameter needs the ::bigint cast — it appears only
-- in an IS NULL test, so Postgres can't infer its type on its own.
-- :name select_sessions
SELECT id, session_day, seq, started_at, ended_at, zones, scheduled, team_index
FROM attendance_sessions
WHERE world = %s AND guild_name = %s AND (%s::bigint IS NULL OR id < %s)
ORDER BY id DESC LIMIT %s;

-- :name select_session
SELECT id, world, guild_name, session_day, seq, started_at, ended_at, zones, scheduled, team_index, uploaders
FROM attendance_sessions WHERE id = %s;

-- :name select_observations
SELECT session_id, character_name, kind, first_seen, last_seen
FROM attendance_observations WHERE session_id = %s;

-- :name select_observations_many
-- The id list binds as ONE array parameter (= ANY) — no composed
-- placeholder strings and no variable-count limits.
SELECT session_id, character_name, kind, first_seen, last_seen
FROM attendance_observations WHERE session_id = ANY(%s);

-- Officer category corrections (see attendance_overrides schema comment).
-- :name upsert_override
INSERT INTO attendance_overrides (session_id, character_name, category, set_by, set_at)
VALUES (%s, %s, %s, %s, floor(extract(epoch from now())))
ON CONFLICT(session_id, character_name) DO UPDATE SET
    category = excluded.category,
    set_by = excluded.set_by,
    set_at = excluded.set_at;

-- :name delete_override
DELETE FROM attendance_overrides WHERE session_id = %s AND LOWER(character_name) = LOWER(%s);

-- :name select_overrides
SELECT character_name, category, set_by, set_at
FROM attendance_overrides WHERE session_id = %s;

-- :name select_overrides_many
-- The id list binds as ONE array parameter (= ANY).
SELECT session_id, character_name, category, set_by, set_at
FROM attendance_overrides WHERE session_id = ANY(%s);

-- Officer window correction — start/end only; day/seq grouping is frozen.
-- :name update_session_window
UPDATE attendance_sessions
   SET started_at = %s, ended_at = %s, updated_at = floor(extract(epoch from now()))
 WHERE id = %s;

-- Officer-authored timelines (attendance_segments): a character's manual
-- rows replace their derived timeline. Replace = delete + insert in one tx.
-- :name delete_segments_for_character
DELETE FROM attendance_segments WHERE session_id = %s AND LOWER(character_name) = LOWER(%s);

-- :name insert_segment
INSERT INTO attendance_segments (session_id, character_name, category, started_at, ended_at, set_by)
VALUES (%s, %s, %s, %s, %s, %s);

-- :name select_segments
SELECT character_name, category, started_at, ended_at, set_by
FROM attendance_segments WHERE session_id = %s ORDER BY started_at;

-- :name select_segments_many
-- The id list binds as ONE array parameter (= ANY).
SELECT session_id, character_name, category, started_at, ended_at, set_by
FROM attendance_segments WHERE session_id = ANY(%s) ORDER BY started_at;

-- :name delete_segments_for_session
DELETE FROM attendance_segments WHERE session_id = %s;

-- Row removal for junk names (mis-parses): kill the character's raid/online
-- observations AND any override in one officer action. Voice rows key by
-- discord id, not character name — untouched by design.
-- :name delete_character_observations
DELETE FROM attendance_observations
WHERE session_id = %s AND LOWER(character_name) = LOWER(%s) AND kind IN ('raid', 'online');

-- The Postgres schema's ON DELETE CASCADE would cover these, but the
-- child rows are still deleted explicitly in the same transaction —
-- explicit beats implicit for an officer-facing destructive action, and
-- it keeps parity with the SQLite-era behaviour.
-- :name delete_observations_for_session
DELETE FROM attendance_observations WHERE session_id = %s;

-- :name delete_overrides_for_session
DELETE FROM attendance_overrides WHERE session_id = %s;

-- :name delete_session
DELETE FROM attendance_sessions WHERE id = %s;

-- Retention: voice rows carry DISCORD IDS of everyone in the raid voice
-- channel, site user or not. They only feed the in_voice flag, so they are
-- swept after VOICE_OBSERVATION_RETENTION_DAYS (the privacy policy says so).
-- :name delete_stale_voice_observations
DELETE FROM attendance_observations WHERE kind = 'voice' AND last_seen < %s;
