-- SQL for backend/server/metrics.py — Prometheus collector COUNT queries.

-- :name count_users_by_access_status
SELECT COUNT(*) AS n FROM users WHERE access_status = %s;

-- :name count_claims_by_status
SELECT COUNT(*) AS n FROM character_claims WHERE status = %s;

-- :name count_visible_encounters
SELECT COUNT(*) AS n FROM encounters WHERE hidden_at IS NULL;

-- :name count_hidden_encounters
SELECT COUNT(*) AS n FROM encounters WHERE hidden_at IS NOT NULL;

-- :name count_raid_encounters
SELECT COUNT(*) FROM raid_encounters;

-- :name count_act_triggers
SELECT COUNT(*) FROM act_triggers;

-- :name count_act_spell_timers
SELECT COUNT(*) FROM act_spell_timers;
