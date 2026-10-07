-- global
-- Supabase lockdown for the reference schemas (aas / classes),
-- mirroring 0006/0010. Same note applies: a future migration adding a
-- table must ENABLE ROW LEVEL SECURITY on it itself.

DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT schemaname, tablename FROM pg_tables
           WHERE schemaname IN ('aas', 'classes')
  LOOP
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', r.schemaname, r.tablename);
  END LOOP;
END $$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA aas, classes FROM anon;
    REVOKE USAGE ON SCHEMA aas, classes FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA aas, classes FROM authenticated;
    REVOKE USAGE ON SCHEMA aas, classes FROM authenticated;
  END IF;
END $$;
