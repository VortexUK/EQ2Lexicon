create schema if not exists items;
set search_path to items, public;

-- Live-DB review 2026-10-07: the two slowest application queries were
-- catalogue lookups on this table. Both indexes are IF NOT EXISTS so they
-- can be pre-built in production with CREATE INDEX CONCURRENTLY (the
-- items heap is ~430 MB; a plain build inside the deploy migration holds a
-- SHARE lock on the table for the build) and this file then records them
-- as a no-op.

-- Rotation simulator (items.sql spell_meta_by_names): spell_name = ANY(...)
-- restricted to spellscroll rows scanned all 69k scrolls — 13 s mean.
CREATE INDEX IF NOT EXISTS idx_items_spellscroll_spell_name
    ON items (spell_name) WHERE typeinfo_name = 'spellscroll';

-- Item search / the /item name fallback / the upgrades raw-ingredient
-- lookup all run displayname_lower LIKE '%...%' over the whole heap
-- (2.4–17 s each). A trigram GIN index serves infix LIKE. Guarded on the
-- extension being available (it is on Supabase and the PG17 installers).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm') THEN
        EXECUTE 'CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_items_name_trgm '
                'ON items USING gin (displayname_lower public.gin_trgm_ops)';
    END IF;
END $$;
