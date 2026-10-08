create schema if not exists parses;
set search_path to parses, public;

-- Tamper reports: one row per (world, encid, uploader). A retried upload
-- updates that row instead of storing another full payload (1,635 rows had
-- grown to 132 MB). Older duplicates are dropped first, keeping the newest.
DELETE FROM tamper_reports a USING tamper_reports b
WHERE a.world = b.world AND a.act_encid = b.act_encid
  AND a.uploader_discord_id = b.uploader_discord_id AND a.id < b.id;
CREATE UNIQUE INDEX IF NOT EXISTS idx_tamper_reports_dedupe
    ON tamper_reports (world, act_encid, uploader_discord_id);

-- ingest_log outlives its encounter as a tombstone: an admin purge must not
-- make the same encid uploadable again. The FK SET NULLs instead of
-- cascading; the retention sweep expires tombstones after their window.
ALTER TABLE ingest_log ALTER COLUMN encounter_id DROP NOT NULL;
DO $$
DECLARE c text;
BEGIN
    SELECT conname INTO c FROM pg_constraint
     WHERE conrelid = 'ingest_log'::regclass AND contype = 'f';
    IF c IS NOT NULL THEN
        EXECUTE format('ALTER TABLE ingest_log DROP CONSTRAINT %I', c);
    END IF;
    ALTER TABLE ingest_log ADD CONSTRAINT ingest_log_encounter_id_fkey
        FOREIGN KEY (encounter_id) REFERENCES encounters(id) ON DELETE SET NULL;
END $$;
