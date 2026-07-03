-- SQL for backend/server/db/raid_schedule.py (async aiosqlite).

-- :name select_teams
SELECT * FROM raid_teams WHERE world = ? AND guild_name = ? ORDER BY team_index;

-- :name select_slots
SELECT * FROM raid_slots WHERE team_id = ? ORDER BY slot_index;

-- :name select_teams_with_twitch
SELECT * FROM raid_teams WHERE twitch_login IS NOT NULL AND twitch_login <> '';

-- Full-replace helpers. Slots are deleted first (via subquery) so we don't
-- depend on ON DELETE CASCADE being enabled on the runtime connection.
-- :name delete_slots_for_guild
DELETE FROM raid_slots WHERE team_id IN (
    SELECT id FROM raid_teams WHERE world = ? AND guild_name = ?
);

-- :name delete_teams_for_guild
DELETE FROM raid_teams WHERE world = ? AND guild_name = ?;

-- :name insert_team
INSERT INTO raid_teams (world, guild_name, team_index, name, primary_tz, twitch_login, updated_by)
VALUES (?, ?, ?, ?, ?, ?, ?);

-- :name insert_slot
INSERT INTO raid_slots (team_id, slot_index, days, start_min, end_min, label)
VALUES (?, ?, ?, ?, ?, ?);
