create schema if not exists parses;
set search_path to parses, public;

-- Fights: the mirror-group an upload belongs to, decided once at ingest
-- (backend/server/parses/fights.py) instead of re-derived from every
-- winning upload on every rankings rebuild. One row per real fight; its
-- member uploads point at it through encounters.fight_id.
--
--   title_key   boss_key(title) — codepoint-normalised so an ACT apostrophe
--               variant can't split one pull into two fights.
--   guild_name  '' when the uploader is unguilded (part of the match key, so
--               NOT NULL keeps the index usable).
--   primary_encounter_id          longest VISIBLE upload — the parses-list
--                                 canonical row.
--   primary_winning_encounter_id  longest visible, verified, winning upload —
--                                 the rankings kill. NULL = this fight ranks
--                                 nothing.
CREATE TABLE IF NOT EXISTS fights (
    id                            bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    world                         text    NOT NULL,
    title_key                     text    NOT NULL,
    guild_name                    text    NOT NULL DEFAULT '',
    first_started_at              bigint  NOT NULL,
    last_started_at               bigint  NOT NULL,
    primary_encounter_id          bigint,
    primary_winning_encounter_id  bigint,
    upload_count                  integer NOT NULL DEFAULT 0,
    updated_at                    bigint  NOT NULL
);

-- Candidate lookup at ingest: same world/title/guild, started within the
-- mirror window of an existing member.
CREATE INDEX IF NOT EXISTS idx_fights_candidates
    ON fights (world, title_key, guild_name, last_started_at);
-- Rankings read + retention sweep walk a world's fights by age.
CREATE INDEX IF NOT EXISTS idx_fights_world_started
    ON fights (world, first_started_at DESC);

ALTER TABLE encounters ADD COLUMN IF NOT EXISTS fight_id bigint REFERENCES fights(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_encounters_fight ON encounters (fight_id);
-- Uploads not yet grouped (pre-migration rows, or an attach that failed):
-- the backfill and the rankings loader pick them up from here.
CREATE INDEX IF NOT EXISTS idx_encounters_unfought ON encounters (world, started_at) WHERE fight_id IS NULL;
