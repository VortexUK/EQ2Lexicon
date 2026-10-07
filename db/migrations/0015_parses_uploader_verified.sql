create schema if not exists parses;
set search_path to parses, public;

-- 1 when the uploading account held an approved claim on logger_name (for
-- the parse's world) at ingest, 0 otherwise. An unverified upload is kept
-- for its uploader but never carries a guild and never ranks. Existing rows
-- predate the check and are treated as verified.
ALTER TABLE encounters ADD COLUMN IF NOT EXISTS uploader_verified smallint NOT NULL DEFAULT 1;
