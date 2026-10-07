create schema if not exists users;
set search_path to users, public;

-- Per-user session revocation counter. The login cookie carries the row's
-- value at login time; a request whose cookie epoch differs is a dead
-- session (kick / deny bump it). Existing cookies carry no epoch and read
-- as 0, which matches the default, so this deploy logs nobody out.
ALTER TABLE users ADD COLUMN IF NOT EXISTS session_epoch integer NOT NULL DEFAULT 0;
