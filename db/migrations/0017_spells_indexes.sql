create schema if not exists spells;
set search_path to spells, public;

-- Raid-buff tier dropdown (spells.sql beneficial_tiers_by_base) matches the
-- mixed-case name column by equality and by a "base <rank>" prefix LIKE;
-- only name_lower was indexed, so it scanned all 167k rows (1.6 s mean).
-- text_pattern_ops serves both the = and the anchored LIKE.
CREATE INDEX IF NOT EXISTS idx_spells_name_pattern ON spells (name text_pattern_ops);
