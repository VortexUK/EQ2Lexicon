-- SQL for backend/server/db/discord_links.py (async aiosqlite).

-- Relinking updates the mapping but deliberately PRESERVES voice_channel_id
-- — an officer fixing a typo'd guild name shouldn't lose the voice config.
-- :name upsert_link
INSERT INTO discord_guild_links (discord_guild_id, world, guild_name, linked_by, updated_at)
VALUES (?, ?, ?, ?, strftime('%s','now'))
ON CONFLICT(discord_guild_id) DO UPDATE SET
    world = excluded.world,
    guild_name = excluded.guild_name,
    linked_by = excluded.linked_by,
    updated_at = excluded.updated_at;

-- :name set_voice_channel
UPDATE discord_guild_links
   SET voice_channel_id = ?, updated_at = strftime('%s','now')
 WHERE discord_guild_id = ?;

-- :name select_link
SELECT discord_guild_id, world, guild_name, voice_channel_id, linked_by, updated_at,
       parses_channel_id, parses_posted_at
FROM discord_guild_links WHERE discord_guild_id = ?;

-- :name delete_link
DELETE FROM discord_guild_links WHERE discord_guild_id = ?;

-- Every link with voice polling on — the Phase 3 poller's work list.
-- :name select_voice_links
SELECT discord_guild_id, world, guild_name, voice_channel_id
FROM discord_guild_links WHERE voice_channel_id IS NOT NULL;

-- Enabling parse posting stamps the watermark to "now" so the poller never
-- floods the channel with history; clearing leaves the watermark alone.
-- :name set_parses_channel
UPDATE discord_guild_links
   SET parses_channel_id = ?,
       parses_posted_at = CASE WHEN ? IS NULL THEN parses_posted_at ELSE strftime('%s','now') END,
       updated_at = strftime('%s','now')
 WHERE discord_guild_id = ?;

-- :name set_parses_posted_at
UPDATE discord_guild_links SET parses_posted_at = ? WHERE discord_guild_id = ?;

-- Every link with parse posting on — the parse poster's work list.
-- :name select_parse_links
SELECT discord_guild_id, world, guild_name, parses_channel_id, parses_posted_at
FROM discord_guild_links WHERE parses_channel_id IS NOT NULL;
