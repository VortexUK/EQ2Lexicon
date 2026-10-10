create schema if not exists parses;
set search_path to parses, public;

-- Characters barred from the boards. Sources: the spell audit (a Census
-- spell list carrying an out-of-era tier — the 2026-10 TLE event bug that
-- let Ancient spells drop and scribe; or a character Census cannot show us,
-- whose spells are therefore unverifiable) or an admin. A flag is enforced
-- by hiding every parse since the cutoff the character played in
-- (encounters.hidden_by = 'spell-audit') and filing a tamper report per
-- parse; clearing the flag restores those parses unless another flagged
-- character is in them. details is JSON text (the offending spells, or an
-- admin note) — text, never jsonb, so it stays byte-exact evidence.
CREATE TABLE IF NOT EXISTS flagged_characters (
    world       text    NOT NULL,
    name_lower  text    NOT NULL,
    name        text    NOT NULL,
    reason      text    NOT NULL,   -- out_of_era_spells | census_hidden | manual
    details     text,
    flagged_at  bigint  NOT NULL,
    flagged_by  text    NOT NULL DEFAULT 'spell-audit',
    cleared_at  bigint,
    cleared_by  text,
    PRIMARY KEY (world, name_lower)
);

-- One row per character the audit has looked at: throttles re-scans (a
-- character is re-checked at most daily) and records the verdict.
CREATE TABLE IF NOT EXISTS spell_audit_scans (
    world       text    NOT NULL,
    name_lower  text    NOT NULL,
    scanned_at  bigint  NOT NULL,
    result      text    NOT NULL,   -- clean | flagged | hidden | error
    PRIMARY KEY (world, name_lower)
);
