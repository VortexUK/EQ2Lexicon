-- SQL for backend/server/parses/db.py (psycopg, parses schema).
-- Schema DDL lives in db/migrations/0002_parses.sql; this file is DML only.
-- The four insert blocks use %(name)s placeholders matched against the
-- models' as_db_params() dicts — change column ↔ field mappings there.

-- :name insert_encounter
INSERT INTO encounters (
    world, act_encid, title, zone,
    started_at, ended_at, duration_s,
    total_damage, encdps, kills, deaths, success_level,
    source_dsn, uploaded_by, guild_name, ingested_at, uploader_verified
) VALUES (
    %(world)s, %(act_encid)s, %(title)s, %(zone)s,
    %(started_at)s, %(ended_at)s, %(duration_s)s,
    %(total_damage)s, %(encdps)s, %(kills)s, %(deaths)s, %(success_level)s,
    %(source_dsn)s, %(uploaded_by)s, %(guild_name)s, %(ingested_at)s, %(uploader_verified)s
)
RETURNING id;

-- :name insert_combatant
INSERT INTO combatants (
    encounter_id, name, ally,
    started_at, ended_at, duration_s,
    damage, damage_perc, kills,
    healed, healed_perc, crit_heals, heals, cure_dispels,
    power_drain, power_replenish,
    dps, encdps, enchps,
    hits, crit_hits, blocked, misses, swings,
    heals_taken, damage_taken, deaths,
    to_hit, crit_dam_perc, crit_heal_perc, crit_types,
    threat_str, threat_delta,
    level, guild_name, cls, ilvl
) VALUES (
    %(encounter_id)s, %(name)s, %(ally)s,
    %(started_at)s, %(ended_at)s, %(duration_s)s,
    %(damage)s, %(damage_perc)s, %(kills)s,
    %(healed)s, %(healed_perc)s, %(crit_heals)s, %(heals)s, %(cure_dispels)s,
    %(power_drain)s, %(power_replenish)s,
    %(dps)s, %(encdps)s, %(enchps)s,
    %(hits)s, %(crit_hits)s, %(blocked)s, %(misses)s, %(swings)s,
    %(heals_taken)s, %(damage_taken)s, %(deaths)s,
    %(to_hit)s, %(crit_dam_perc)s, %(crit_heal_perc)s, %(crit_types)s,
    %(threat_str)s, %(threat_delta)s,
    %(level)s, %(guild_name)s, %(cls)s, %(ilvl)s
)
RETURNING id;

-- :name update_combatant_snapshot
UPDATE combatants SET level = %s, guild_name = %s, cls = %s, ilvl = %s
WHERE encounter_id = %s AND name = %s;

-- :name update_combatant_is_player
UPDATE combatants SET is_player = %s WHERE id = %s;

-- Only ally rows carry a classification (enemies are NULL by design), and
-- rows already NULL need no write — every skipped row is a heap tuple plus
-- seven index entries not rewritten.
-- :name invalidate_is_player_cache
UPDATE combatants SET is_player = NULL
WHERE ally = 1 AND is_player IS NOT NULL;

-- :name invalidate_is_player_for_zone
UPDATE combatants SET is_player = NULL
WHERE ally = 1 AND is_player IS NOT NULL
  AND encounter_id IN (SELECT id FROM encounters WHERE lower(zone) = lower(%s));

-- :name insert_damage_type
INSERT INTO damage_types (
    combatant_id, grouping_label, damage_type,
    started_at, ended_at, duration_s,
    damage, encdps, char_dps, dps,
    average, median, min_hit, max_hit,
    hits, crit_hits, blocked, misses, swings,
    to_hit, average_delay, crit_perc, crit_types
) VALUES (
    %(combatant_id)s, %(grouping_label)s, %(damage_type)s,
    %(started_at)s, %(ended_at)s, %(duration_s)s,
    %(damage)s, %(encdps)s, %(char_dps)s, %(dps)s,
    %(average)s, %(median)s, %(min_hit)s, %(max_hit)s,
    %(hits)s, %(crit_hits)s, %(blocked)s, %(misses)s, %(swings)s,
    %(to_hit)s, %(average_delay)s, %(crit_perc)s, %(crit_types)s
);

-- :name insert_attack_type
INSERT INTO attack_types (
    combatant_id, victim, swing_type, attack_name,
    started_at, ended_at, duration_s,
    damage, encdps, char_dps, dps,
    average, median, min_hit, max_hit, resist,
    hits, crit_hits, blocked, misses, swings,
    to_hit, average_delay, crit_perc, crit_types
) VALUES (
    %(combatant_id)s, %(victim)s, %(swing_type)s, %(attack_name)s,
    %(started_at)s, %(ended_at)s, %(duration_s)s,
    %(damage)s, %(encdps)s, %(char_dps)s, %(dps)s,
    %(average)s, %(median)s, %(min_hit)s, %(max_hit)s, %(resist)s,
    %(hits)s, %(crit_hits)s, %(blocked)s, %(misses)s, %(swings)s,
    %(to_hit)s, %(average_delay)s, %(crit_perc)s, %(crit_types)s
);

-- :name mark_ingested
INSERT INTO ingest_log (world, act_encid, encounter_id, ingested_at, source_dsn)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (world, act_encid) DO NOTHING;

-- The (world, act_encid) primary key is the insert lock: claim it FIRST in
-- the ingest transaction (encounter_id NULL), then bind the encounter id.
-- A concurrent retry loses the claim and reports 'skipped' instead of a
-- UNIQUE violation mid-transaction.
-- :name claim_encid
INSERT INTO ingest_log (world, act_encid, encounter_id, ingested_at, source_dsn)
VALUES (%s, %s, NULL, %s, %s)
ON CONFLICT (world, act_encid) DO NOTHING
RETURNING act_encid;

-- :name bind_ingest_log
UPDATE ingest_log SET encounter_id = %s WHERE world = %s AND act_encid = %s;

-- Tombstones (encounter purged) expire after their window.
-- :name expire_ingest_tombstones
DELETE FROM ingest_log WHERE encounter_id IS NULL AND ingested_at < %s;

-- :name expire_tamper_reports
DELETE FROM tamper_reports
WHERE reported_at < %s OR (acknowledged_at IS NOT NULL AND acknowledged_at < %s);

-- ---------------------------------------------------------------------------
-- Lookup helpers
-- ---------------------------------------------------------------------------

-- :name check_is_ingested
SELECT 1 FROM ingest_log WHERE world = %s AND act_encid = %s LIMIT 1;

-- :name find_encounter_by_act_encid
SELECT * FROM encounters WHERE world = %s AND act_encid = %s LIMIT 1;

-- :name recent_encounters_by_zone
SELECT * FROM encounters
WHERE world = %s AND zone = %s
ORDER BY started_at DESC
LIMIT %s;

-- :name recent_encounters_all
SELECT * FROM encounters WHERE world = %s ORDER BY started_at DESC LIMIT %s;

-- {where} = "WHERE …" composed in Python; embeds a correlated subquery for
-- the player_count column. (Comment kept OUTSIDE the block — the composed
-- fragment contains %s and psycopg counts placeholders inside comments.)
-- :name list_encounters_for_admin
SELECT e.id, e.title, e.zone, e.guild_name, e.uploaded_by, e.started_at,
       e.duration_s, e.success_level, e.hidden_at, e.hidden_by, e.client_warnings,
       (SELECT COUNT(*) FROM combatants c
          WHERE c.encounter_id = e.id AND c.ally = 1
            AND c.name != '' AND c.name != 'Unknown'
            AND strpos(c.name, ' ') = 0) AS player_count
FROM encounters e
{where}
ORDER BY e.started_at DESC
LIMIT %s;

-- :name delete_encounter
DELETE FROM encounters WHERE id = %s;

-- :name soft_delete_encounter
UPDATE encounters SET hidden_at = %s, hidden_by = %s WHERE id = %s AND hidden_at IS NULL;

-- :name unhide_encounter
UPDATE encounters SET hidden_at = NULL, hidden_by = NULL WHERE id = %s AND hidden_at IS NOT NULL;

-- :name set_encounter_guild_name
UPDATE encounters SET guild_name = %s WHERE id = %s;

-- :name get_combatants_for_encounter
SELECT * FROM combatants WHERE encounter_id = %s ORDER BY damage DESC;

-- Batched form of get_combatants_for_encounter: the rankings rebuild fetches
-- every primary kill's combatants in ONE query (= ANY). Narrowed to exactly
-- the columns the rankings / export / character-rankings pipelines read, and
-- shaped to be COVERED by idx_combatants_rankings_cover so the rankings read
-- is index-only (SELECT * would random-page ~150k wide rows).
-- :name get_combatants_for_encounters
SELECT encounter_id, name, ally, is_player, cls, level, ilvl, guild_name,
       encdps, enchps, damage, healed, deaths
FROM combatants WHERE encounter_id = ANY(%s) ORDER BY encounter_id, damage DESC;

-- :name get_top_attacks_by_swing_type
SELECT * FROM attack_types
WHERE combatant_id = %s AND swing_type = ANY(%s)
ORDER BY damage DESC
LIMIT %s;

-- :name get_top_cures
SELECT * FROM attack_types
WHERE combatant_id = %s AND swing_type = ANY(%s)
ORDER BY hits DESC, damage DESC
LIMIT %s;

-- :name get_top_threats
SELECT * FROM attack_types
WHERE combatant_id = %s
  AND swing_type = ANY(%s)
  AND attack_name <> 'All'
ORDER BY damage DESC
LIMIT %s;

-- :name get_damage_types_for_combatant
SELECT * FROM damage_types
WHERE combatant_id = %s
ORDER BY damage DESC;

-- Bulk variants for the parse detail page: one statement per table for
-- every combatant in the encounter instead of five per combatant (a
-- 30-combatant raid parse was ~150 round trips). ROW_NUMBER() applies the
-- same per-combatant ordering + LIMIT as the single-row blocks above.

-- :name get_top_attacks_bulk
SELECT * FROM (
    SELECT a.*, ROW_NUMBER() OVER (PARTITION BY combatant_id ORDER BY damage DESC) AS rn
    FROM attack_types a
    WHERE combatant_id = ANY(%s) AND swing_type = ANY(%s)
) t WHERE rn <= %s
ORDER BY combatant_id, rn;

-- :name get_top_cures_bulk
SELECT * FROM (
    SELECT a.*, ROW_NUMBER() OVER (PARTITION BY combatant_id ORDER BY hits DESC, damage DESC) AS rn
    FROM attack_types a
    WHERE combatant_id = ANY(%s) AND swing_type = ANY(%s)
) t WHERE rn <= %s
ORDER BY combatant_id, rn;

-- :name get_top_threats_bulk
SELECT * FROM (
    SELECT a.*, ROW_NUMBER() OVER (PARTITION BY combatant_id ORDER BY damage DESC) AS rn
    FROM attack_types a
    WHERE combatant_id = ANY(%s) AND swing_type = ANY(%s) AND attack_name <> 'All'
) t WHERE rn <= %s
ORDER BY combatant_id, rn;

-- :name get_damage_types_bulk
SELECT * FROM damage_types
WHERE combatant_id = ANY(%s)
ORDER BY combatant_id, damage DESC;

-- ---------------------------------------------------------------------------
-- client_warnings + tamper_reports
-- ---------------------------------------------------------------------------

-- :name set_encounter_client_warnings
UPDATE encounters SET client_warnings = %s WHERE id = %s;

-- :name insert_tamper_report
INSERT INTO tamper_reports (
    world, act_encid, title, zone,
    started_at, ended_at, duration_s,
    total_damage, encdps,
    reason, reported_at,
    uploader_logger_name, uploader_discord_id, uploader_discord_name,
    guild_name, payload_json
) VALUES (
    %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s,
    %s, %s, %s,
    %s, %s
)
ON CONFLICT (world, act_encid, uploader_discord_id) DO UPDATE SET
    reason = excluded.reason,
    reported_at = excluded.reported_at,
    payload_json = excluded.payload_json,
    acknowledged_at = NULL,
    acknowledged_by = NULL
RETURNING id;

-- {where} composed in Python (filters: world / reason / pending|ack|all).
-- Comment kept OUTSIDE the block — see list_encounters_for_admin.
-- :name list_tamper_reports
SELECT id, world, act_encid, title, zone,
       started_at, ended_at, duration_s,
       total_damage, encdps,
       reason, reported_at,
       uploader_logger_name, uploader_discord_id, uploader_discord_name,
       guild_name, payload_json,
       acknowledged_at, acknowledged_by
FROM tamper_reports
{where}
ORDER BY reported_at DESC
LIMIT %s;

-- :name acknowledge_tamper_report
UPDATE tamper_reports
   SET acknowledged_at = %s, acknowledged_by = %s
 WHERE id = %s AND acknowledged_at IS NULL;

-- :name acknowledge_tamper_reports_bulk
UPDATE tamper_reports
   SET acknowledged_at = %s, acknowledged_by = %s
 WHERE id = ANY(%s) AND acknowledged_at IS NULL;

-- :name count_pending_tamper_reports
SELECT COUNT(*) AS n FROM tamper_reports WHERE acknowledged_at IS NULL;

-- :name count_pending_tamper_reports_for_world
SELECT COUNT(*) AS n FROM tamper_reports WHERE world = %s AND acknowledged_at IS NULL;

-- Ack EVERY pending report for a world in one statement — the spam-flood
-- escape hatch (a hammering uploader can create more than the 500-id batch
-- endpoint can clear in a sane number of round-trips).
-- :name acknowledge_all_pending_tamper_reports
UPDATE tamper_reports
   SET acknowledged_at = %s, acknowledged_by = %s
 WHERE world = %s AND acknowledged_at IS NULL;

-- Hard-delete already-reviewed reports for a world to reclaim space. Pending
-- rows are never touched.
-- :name delete_acknowledged_tamper_reports
DELETE FROM tamper_reports WHERE world = %s AND acknowledged_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Tiered detail retention (cleanup sweep)
-- ---------------------------------------------------------------------------

-- Candidate encounters whose breakdown rows may be due for pruning: older
-- than the LONGEST tier, detail not yet pruned, not soft-deleted. The sweep
-- classifies each zone in Python (curated raid 30d / group-instance 14d /
-- other 7d) and prunes the subset whose tier cutoff has actually passed.
-- :name select_detail_prune_candidates
SELECT id, zone, started_at FROM encounters
WHERE detail_pruned_at IS NULL AND started_at < %s
ORDER BY started_at
LIMIT %s;

-- :name prune_detail_attack_types
DELETE FROM attack_types
WHERE combatant_id IN (SELECT id FROM combatants WHERE encounter_id = ANY(%s));

-- :name prune_detail_damage_types
DELETE FROM damage_types
WHERE combatant_id IN (SELECT id FROM combatants WHERE encounter_id = ANY(%s));

-- :name mark_detail_pruned
UPDATE encounters SET detail_pruned_at = %s WHERE id = ANY(%s);
