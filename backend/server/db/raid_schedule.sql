-- SQL for backend/server/db/raid_schedule.py (psycopg, users schema).

-- :name select_teams
SELECT * FROM raid_teams WHERE world = %s AND guild_name = %s ORDER BY team_index;

-- :name select_slots
SELECT * FROM raid_slots WHERE team_id = %s ORDER BY slot_index;

-- :name select_teams_for_guilds
-- The recruiting browse page: every listed guild's teams in one statement.
SELECT * FROM raid_teams WHERE world = %s AND guild_name = ANY(%s) ORDER BY guild_name, team_index;

-- :name select_slots_for_teams
SELECT * FROM raid_slots WHERE team_id = ANY(%s) ORDER BY team_id, slot_index;

-- :name select_teams_with_twitch
SELECT * FROM raid_teams WHERE twitch_login IS NOT NULL AND twitch_login <> '';

-- Full-replace helpers. Slots are deleted first (via subquery) so we don't
-- depend on ON DELETE CASCADE being enabled on the runtime connection.
-- :name delete_slots_for_guild
DELETE FROM raid_slots WHERE team_id IN (
    SELECT id FROM raid_teams WHERE world = %s AND guild_name = %s
);

-- :name delete_teams_for_guild
DELETE FROM raid_teams WHERE world = %s AND guild_name = %s;

-- :name insert_team
INSERT INTO raid_teams (world, guild_name, team_index, name, primary_tz, twitch_login, updated_by)
VALUES (%s, %s, %s, %s, %s, %s, %s)
RETURNING id;

-- :name insert_slot
INSERT INTO raid_slots (team_id, slot_index, days, start_min, end_min, label)
VALUES (%s, %s, %s, %s, %s, %s);
