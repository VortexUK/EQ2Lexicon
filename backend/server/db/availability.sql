-- SQL for backend/server/db/availability.py (AvailabilityStore).

-- :name select_range
SELECT day, status FROM user_availability
WHERE discord_id = ? AND day >= ? AND day <= ?
ORDER BY day;

-- :name upsert_day
INSERT INTO user_availability (discord_id, day, status)
VALUES (?, ?, ?)
ON CONFLICT(discord_id, day) DO UPDATE SET status = excluded.status;

-- :name delete_day
DELETE FROM user_availability WHERE discord_id = ? AND day = ?;

-- :name select_statuses_for_day
SELECT discord_id, status FROM user_availability
WHERE day = ?;
