-- SQL for backend/server/db/guild_recruitment.py (psycopg, users schema).
--
-- Rows are KEYED by census guild id (rename-proof); guild_name is a
-- lookup/display column kept fresh by officer saves and the daily census
-- sweep. purge_name_conflicts runs before any write that (re)binds a name
-- to an id: census name ownership is unique per world, so a row holding
-- the same name under a DIFFERENT id is provably stale (disbanded guild
-- whose name was recycled) and is removed to keep the unique name index
-- satisfiable.

-- :name select_profile
-- Explicit columns only — the logo bytea is never selected here; readers
-- get a has_logo flag instead.
SELECT guild_id, guild_name, recruiting, description, classes_json, tags_json,
       contacts_json, discord_url, updated_by, updated_at,
       (logo IS NOT NULL) AS has_logo, logo_uploaded_by, logo_uploaded_at
FROM guild_recruitment WHERE world = %s AND guild_name = %s;

-- :name purge_name_conflicts
DELETE FROM guild_recruitment WHERE world = %s AND guild_name = %s AND guild_id <> %s;

-- :name upsert_profile
-- Profile columns ONLY — the logo columns are owned by upsert_logo and
-- must never be clobbered by a profile save. Conflict on the ID key so a
-- renamed guild's save refreshes guild_name on its existing row.
INSERT INTO guild_recruitment (
    world, guild_id, guild_name, recruiting, description, classes_json,
    tags_json, contacts_json, discord_url, updated_by, updated_at
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, floor(extract(epoch from now())))
ON CONFLICT(world, guild_id) DO UPDATE SET
    guild_name = excluded.guild_name,
    recruiting = excluded.recruiting,
    description = excluded.description,
    classes_json = excluded.classes_json,
    tags_json = excluded.tags_json,
    contacts_json = excluded.contacts_json,
    discord_url = excluded.discord_url,
    updated_by = excluded.updated_by,
    updated_at = floor(extract(epoch from now()));

-- :name select_logo
SELECT logo, logo_media_type, logo_uploaded_at
FROM guild_recruitment
WHERE world = %s AND guild_name = %s AND logo IS NOT NULL;

-- :name upsert_logo
-- Logo columns ONLY — an insert on a guild with no profile row leaves the
-- profile columns at their schema defaults ("not recruiting", empty).
INSERT INTO guild_recruitment (
    world, guild_id, guild_name, logo, logo_media_type, logo_uploaded_by, logo_uploaded_at
)
VALUES (%s, %s, %s, %s, %s, %s, floor(extract(epoch from now())))
ON CONFLICT(world, guild_id) DO UPDATE SET
    guild_name = excluded.guild_name,
    logo = excluded.logo,
    logo_media_type = excluded.logo_media_type,
    logo_uploaded_by = excluded.logo_uploaded_by,
    logo_uploaded_at = floor(extract(epoch from now()));

-- :name clear_logo
UPDATE guild_recruitment
SET logo = NULL, logo_media_type = NULL, logo_uploaded_by = NULL, logo_uploaded_at = NULL
WHERE world = %s AND guild_name = %s AND logo IS NOT NULL;

-- :name select_recruiting
SELECT guild_id, guild_name, description, classes_json, tags_json, contacts_json,
       discord_url, updated_at,
       (logo IS NOT NULL) AS has_logo, logo_uploaded_at
FROM guild_recruitment
WHERE world = %s AND recruiting = 1
ORDER BY updated_at DESC;

-- Sweep support: every listed row's identity for the daily census
-- existence check.
-- :name select_listed_worlds
SELECT DISTINCT world FROM guild_recruitment WHERE recruiting = 1;

-- :name select_listed_ids
SELECT guild_id, guild_name FROM guild_recruitment WHERE world = %s AND recruiting = 1;

-- :name update_guild_name
UPDATE guild_recruitment SET guild_name = %s WHERE world = %s AND guild_id = %s AND guild_name <> %s;

-- :name delist_guild
UPDATE guild_recruitment SET recruiting = 0 WHERE world = %s AND guild_id = %s AND recruiting = 1;
