-- SQL for backend/server/db/availability.py (AvailabilityStore).

-- Stored 'available' rows are an explicit newest-wins clear, not calendar
-- content — the calendar renders them as the default.
-- :name select_range
SELECT day, status FROM user_availability
WHERE discord_id = ? AND day >= ? AND day <= ? AND status != 'available'
ORDER BY day;

-- :name upsert_day
INSERT INTO user_availability (discord_id, day, status, updated_at)
VALUES (?, ?, ?, strftime('%s','now'))
ON CONFLICT(discord_id, day) DO UPDATE SET
    status = excluded.status,
    updated_at = excluded.updated_at;

-- :name delete_day
DELETE FROM user_availability WHERE discord_id = ? AND day = ?;

-- :name select_statuses_for_day
SELECT discord_id, status FROM user_availability
WHERE day = ?;

-- :name select_statuses_for_day_with_times
SELECT discord_id, status, updated_at FROM user_availability
WHERE day = ?;

-- :name char_upsert_day
INSERT INTO character_availability (world, character_name, day, status, set_by)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(world, character_name, day)
DO UPDATE SET status = excluded.status, set_by = excluded.set_by, updated_at = strftime('%s','now');

-- :name char_delete_day
DELETE FROM character_availability WHERE world = ? AND character_name = ? AND day = ?;

-- :name char_statuses_for_day
SELECT character_name, status FROM character_availability
WHERE world = ? AND day = ?;

-- :name char_statuses_for_day_with_times
SELECT character_name, status, updated_at FROM character_availability
WHERE world = ? AND day = ?;
