-- SQL for backend/eq2db/spells.py (Postgres `spells` schema; unqualified —
-- the catalogue's connections set search_path). Schema DDL lives in
-- db/migrations/0008_spells.sql; this file is DML only.

-- standalone fragment; spliced via f-string in Python where needed.
-- :name select_cols
id, name, name_lower, base_name, base_name_lower,
tier, tier_name, type, typeid, level, given_by, crc, beneficial,
passes_spellcheck,
cast_secs, recast_secs, recovery_secs,
target_type, aoe_radius, max_targets,
description, icon_id, icon_backdrop,
effects, last_update

-- :name upsert
INSERT INTO spells (
    id, name, name_lower, base_name, base_name_lower,
    tier, tier_name, type, typeid, level, given_by, crc, beneficial,
    passes_spellcheck,
    cast_secs, recast_secs, recovery_secs,
    target_type, aoe_radius, max_targets,
    description, icon_id, icon_backdrop,
    effects,
    last_update
) VALUES (
    %(id)s, %(name)s, %(name_lower)s, %(base_name)s, %(base_name_lower)s,
    %(tier)s, %(tier_name)s, %(type)s, %(typeid)s, %(level)s, %(given_by)s, %(crc)s, %(beneficial)s,
    %(passes_spellcheck)s,
    %(cast_secs)s, %(recast_secs)s, %(recovery_secs)s,
    %(target_type)s, %(aoe_radius)s, %(max_targets)s,
    %(description)s, %(icon_id)s, %(icon_backdrop)s,
    %(effects)s,
    %(last_update)s
)
ON CONFLICT (id) DO UPDATE SET
    name = excluded.name, name_lower = excluded.name_lower,
    base_name = excluded.base_name, base_name_lower = excluded.base_name_lower,
    tier = excluded.tier, tier_name = excluded.tier_name,
    type = excluded.type, typeid = excluded.typeid,
    level = excluded.level, given_by = excluded.given_by,
    crc = excluded.crc, beneficial = excluded.beneficial,
    passes_spellcheck = excluded.passes_spellcheck,
    cast_secs = excluded.cast_secs, recast_secs = excluded.recast_secs,
    recovery_secs = excluded.recovery_secs,
    target_type = excluded.target_type, aoe_radius = excluded.aoe_radius,
    max_targets = excluded.max_targets,
    description = excluded.description,
    icon_id = excluded.icon_id, icon_backdrop = excluded.icon_backdrop,
    effects = excluded.effects,
    last_update = excluded.last_update;

-- :name count
SELECT COUNT(*) AS n FROM spells;

-- :name find_by_id
SELECT {cols} FROM spells WHERE id = %s LIMIT 1;

-- :name find_by_ids
SELECT {cols} FROM spells WHERE id = ANY(%s);

-- :name upgradeable_crcs
-- Of the given CRCs, return those whose spell line has more than one tier
-- (Apprentice → Grandmaster, …) — i.e. genuinely upgradeable spells. Single-tier
-- abilities (Cure, Resurrect, Soothe, …) have one tier_name and are excluded.
-- idx_spells_crc keeps the ANY + GROUP BY fast.
SELECT crc FROM spells WHERE crc = ANY(%s)
GROUP BY crc HAVING COUNT(DISTINCT tier_name) > 1;

-- A (crc, tier) pair has one row PER LEVEL-SCALED VARIANT (level 70 / 100 /
-- 110 / 120 / …). The high-level variants from later expansions carry 0.0%%
-- placeholder effect values; the earliest non-zero level (the TLE-era row) is
-- the populated, deployment-relevant one. Without a deterministic ORDER BY,
-- LIMIT 1 returned an arbitrary variant — sometimes a 0.0%% placeholder (the
-- "Increases Max Health by 0.0%%" AA-tooltip bug). Sort level=0 rows last.
-- :name beneficial_group_by_names
-- The rotation simulator's group-buff universe: beneficial spells with a
-- group/raid/single-ally target scope inside the era's level band.
-- tier <= 9 (Master): Grandmaster/Ancient/Celestial are post-era research
-- tiers whose rows carry modern live-game values — never era-correct.
SELECT {cols} FROM spells
WHERE name = ANY(%s) AND beneficial = 1
  AND target_type IN ('group', 'raid', 'other')
  AND level BETWEEN 1 AND %s AND tier <= 9;

-- :name beneficial_tiers_by_base
-- Every era tier row of one raid-buff LINE (name = base or "base <rank>")
-- for the tier dropdown. Same universe filters as
-- beneficial_group_by_names; the caller picks the era rank + one row per
-- tier name. LIKE is case-sensitive in PG — the pattern is built from the
-- stored mixed-case name itself, so exact-case matching is correct here.
SELECT {cols} FROM spells
WHERE (name = %s OR name LIKE %s) AND beneficial = 1
  AND target_type IN ('group', 'raid', 'other')
  AND level BETWEEN 1 AND %s AND tier <= 9;

-- :name find_by_crc_tier_bands
-- All real-level band rows for an AA rank (level bands 70/100/110…) —
-- the game linearly interpolates a character's value between bands.
SELECT {cols} FROM spells WHERE crc = %s AND tier = %s AND level > 0 ORDER BY level ASC;

-- :name find_by_crc_and_tier
SELECT {cols} FROM spells WHERE crc = %s AND tier = %s
ORDER BY CASE WHEN level = 0 THEN 9999 ELSE level END ASC LIMIT 1;

-- :name find_by_crc_highest_tier
-- NULLS LAST: SQLite sorted NULL tiers last on DESC; PG defaults them first.
SELECT {cols} FROM spells WHERE crc = %s
ORDER BY tier DESC NULLS LAST, CASE WHEN level = 0 THEN 9999 ELSE level END ASC LIMIT 1;

-- :name find_by_name_exact
SELECT {cols} FROM spells WHERE name_lower = %s ORDER BY level;

-- :name find_by_name_like
SELECT {cols} FROM spells WHERE name_lower LIKE %s ESCAPE '\' ORDER BY level;
