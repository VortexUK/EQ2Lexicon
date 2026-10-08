create schema if not exists users;
set search_path to users, public;

-- Guild-configurable officer ranks. NULL = the site default (Census rank
-- ids 0 and 1); a guild leader can list exactly which rank ids count as
-- officers (rank 0, the leader, is always included server-side). Some
-- guilds run three leadership ranks (junior / senior / leader) and were
-- stuck with only the top two.
ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS officer_rank_ids integer[];
