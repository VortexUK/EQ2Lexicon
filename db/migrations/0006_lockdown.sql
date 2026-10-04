-- global
-- Supabase lockdown (belt and braces). The app connects as the schema
-- OWNER, which bypasses RLS — enabling RLS on every family table plus
-- revoking the PostgREST roles means the auto-generated API can't read
-- the data even if a schema were ever added to the exposed list
-- (`public` stays the only exposed schema, and it is empty).
--
-- NOTE: this runs once (migration ledger). A future migration that adds
-- a table must ENABLE ROW LEVEL SECURITY on it itself.

DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT schemaname, tablename FROM pg_tables
           WHERE schemaname IN ('users', 'parses', 'census', 'zones', 'raids')
  LOOP
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', r.schemaname, r.tablename);
  END LOOP;
END $$;

-- Supabase ships anon / authenticated; plain Postgres (local dev, tests,
-- CI) doesn't — guard every revoke so the migration runs everywhere.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA users, parses, census, zones, raids FROM anon;
    REVOKE USAGE ON SCHEMA users, parses, census, zones, raids FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA users, parses, census, zones, raids FROM authenticated;
    REVOKE USAGE ON SCHEMA users, parses, census, zones, raids FROM authenticated;
  END IF;
END $$;
