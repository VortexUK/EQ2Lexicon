-- global
-- Supabase lockdown for the Phase-2 catalogue schemas (items / spells /
-- recipes), mirroring 0006_lockdown.sql. The app connects as the schema
-- OWNER (bypasses RLS); enabling RLS + revoking the PostgREST roles keeps
-- the auto-generated API blind to these schemas too. 0006's note applies
-- here as well: a future migration that adds a table must ENABLE ROW LEVEL
-- SECURITY on it itself.

DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT schemaname, tablename FROM pg_tables
           WHERE schemaname IN ('items', 'spells', 'recipes')
  LOOP
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', r.schemaname, r.tablename);
  END LOOP;
END $$;

-- Supabase ships anon / authenticated; plain Postgres (local dev, tests,
-- CI) doesn't — guard every revoke so the migration runs everywhere.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA items, spells, recipes FROM anon;
    REVOKE USAGE ON SCHEMA items, spells, recipes FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA items, spells, recipes FROM authenticated;
    REVOKE USAGE ON SCHEMA items, spells, recipes FROM authenticated;
  END IF;
END $$;
