---
paths:
  - "backend/pg.py"
  - "backend/pg_migrate.py"
  - "backend/db_catalogue.py"
  - "backend/sql_loader.py"
  - "backend/eq2db/**"
  - "backend/server/db/**"
  - "backend/**/*.sql"
  - "db/migrations/**"
  - "tests/fixtures/**"
---

# Postgres: catalogues, stores, migrations

## Conventions (migrations, sidecars, stores, tests)

- **Migrations**: `db/migrations/NNNN_<family>[_<topic>].sql`, applied in filename order by `backend/pg_migrate.py` at startup (a deploy is a migration run). The ledger `public.schema_migrations` is keyed by filename with no checksum, so editing an applied file changes nothing in prod while tests (fresh database) still pass: never edit an applied migration, add a new one.
- A family file starts with exactly `create schema if not exists <family>;` then `set search_path to <family>, public;`, followed by unqualified DDL. A file whose first line is `-- global` is exempt. Base family files carry a `-- seeds` marker (the test leaser replays everything after it).
- Every statement is idempotent (`IF NOT EXISTS`, `DROP ... IF EXISTS`, `DO $$ ... $$` guards). New tables must `ENABLE ROW LEVEL SECURITY` themselves. Timestamps are `bigint` epoch seconds; ids are `bigint GENERATED ALWAYS AS IDENTITY`.
- Each file is one transaction, so `CREATE INDEX CONCURRENTLY` cannot go in a migration: build it concurrently in prod first and let the `IF NOT EXISTS` file record a no-op (see `db/migrations/0016_items_indexes.sql`).
- Keep DDL lock-short. Deploys overlap, and the old container's read transactions block the new container's DDL; the runner uses a 2 s `lock_timeout` and retries.
- **SQL sidecars**: DML lives in a `.sql` file next to its module, loaded with `_SQL = load_sql(__file__)` (`backend/sql_loader.py`). Blocks start with `-- :name <id>`, use `%s` params, stay unqualified, and must not contain `%` inside a comment. Spec: `docs/architecture/sql-loader.md`.
- **Stores**: `XStore(PgStoreBase)` in `backend/server/db/` with a module-level `store`; register it in `ALL_STORES` in `backend/server/db/__init__.py` (the test fixtures iterate it to re-point schemas).
- **Guard tests**: `tests/test_pg_migrate.py` (header convention, idempotency), `tests/test_sql_blocks.py` (every `_SQL["name"]` resolves), `tests/server/test_db_facade.py` (facade coverage). Tests run only against local PostgreSQL; `tests/fixtures/pg.py` refuses any other host.

## Files

| File | Purpose |
|---|---|
| `backend/eq2db/spells.py` | Spell catalogue (Postgres `spells` schema) — `SpellCatalogue` class (shared `catalogue` instance): strip_roman, unique_highest_entries, load_blocklist, find_by_ids, find_by_crc, spell_to_row, upsert_spells. Every eq2db module follows this convention: pure helpers are staticmethods with full bodies in the class, DB reads are instance methods on a per-instance identity (`schema` for the Postgres families, `path` for the committed SQLite reference data aas/classes), and tests lease scratch schemas (fixtures in `tests/fixtures/catalogues_db.py` + `tests/fixtures/pg.py`). ALL data families live in **Supabase Postgres** — there is NO SQLite anywhere in the backend (Phase 3, 2026-10) — one database, one schema per family. DDL is owned by `db/migrations/NNNN_<family>.sql`, applied by `backend/pg_migrate.py` from the app lifespan (deploy = migrate; ledger `public.schema_migrations` + advisory lock). Connections come from `backend/pg.py` (lifespan-owned psycopg pools, pool-if-open-else-direct; schema passed to `connection(schema)` / `aconnection(schema)` / `getconn(schema)` so all SQL stays unqualified — the `SET search_path` is session-level and only sent when the physical connection's remembered schema differs; `autocommit=True` (or `PgStoreBase._read()`) makes a single-statement read one round trip instead of BEGIN+query+COMMIT; raw SETs on a pooled connection must be followed by `pg.forget_schema(conn)`, multi-schema transactions use `local_search_path_sql`; `%s` params, dict rows). The users domain stores are `XStore(PgStoreBase)`; `CensusStore` / `ParsesStore` / `ZoneCatalogue` / `RaidCatalogue` / `ItemCatalogue` / `SpellCatalogue` / `RecipeCatalogue` are `PgCatalogue` (catalogues add `READY_TABLE`/`ready()` — the route-level 503 gate that replaced the file-exists checks) — `init_db()` KEEPS ITS NAME but returns a pooled schema-scoped connection proxy (close() returns to pool). The `backend.server.db` facade re-exports are unchanged; tests lease scratch schemas via `tests/fixtures/pg.py` (TEST_DATABASE_URL, local PG17) and re-point `store.schema`. |
| `backend/eq2db/recipes.py` | Recipe catalogue (~66k rows, Postgres `recipes` schema) — `RecipeCatalogue`: find_by_id, find_by_name, find_by_spell, find_spells_by_tier, find_by_output_id. Secondary components stored as JSON array (text). `out_level` is loader-owned (scripts/backfill_recipe_levels.py cross-schema UPDATE), never written by the upsert. |
| `backend/eq2db/aas.py` | AA catalogue (Postgres `aas` schema; migration 0012 seeds the full dataset). Tables `aa_trees` (tree_type + max_points precomputed at build) + `aa_nodes` (incl. per-node unlock rules: points_to_unlock, classification_points_required, first_parent_id/tier) + `aa_limits` (per-xpac caps + `visible_rows` era node curation from aa_limits.json — pre-SF xpacs hide class rows 5-6 / subclass rows 16+19, verified against live Wuoshi census). Accessors: `load_tree_index`, `tree_node_costs`, `tree_max_points`, `total_max_points`, `get_tree`, `xpac_limits` (short-code alias tolerant; returns aa_cap + unlocked_trees + visible_rows); `detect_tree_type` is build-time only. Refresh: `scripts/download_aa_trees.py` (tree JSONs are local intermediates, gitignored) then `scripts/build_aas_db.py` (upserts into the schema at DATABASE_URL). Era filtering surfaces via `/api/aa/config`.`visible_rows` → `filterTreeForEra` in CharacterAAsTab. |
| `backend/eq2db/zones.py` | Zone catalogue (~1124 rows, Postgres `zones` schema) — `ZoneCatalogue` over frozen dataclass models (`Zone`, `ZoneEncounter`, `ZoneEncounterMob`, `FeaturedRaid*`). Tables: `zones`, `zone_types`, `zone_aliases`, `zone_encounters`, `zone_encounter_mobs` + the featured-raid trio. Lookups: `find_by_name`, `list_by_expansion`, `list_by_event`, `list_by_type`, `list_bosses_for_zone`, `find_zones_by_boss`. Zone metadata from `scripts/dev/eq2_zones.cleaned.json`; rebuild via `scripts/build_zones_db.py`. Boss data is curator-managed in-place. |
| `backend/eq2db/raids.py` | Raid-strategy catalogue (Postgres `raids` schema) — `RaidCatalogue`: `raid_zones` + `raid_encounters` (markdown blob per encounter) + `raid_encounter_revisions` + ACT trigger/spell-timer tables (helpers folded in from the former raids_act.py). `SOURCE_*` provenance tokens mirrored as class attributes. |

## Gotchas

- **A new migration must work on two shapes**: a fresh database that has just run every earlier file, and production as it stands. Tests only exercise the first.
- **When a deploy fails under overlap, check the connection budget before suspecting locks.** The session pooler caps clients and one container can hold up to 11; `SELECT application_name, count(*) FROM pg_stat_activity GROUP BY 1` shows it. Long read transactions in the app (rankings rebuild, bulk backfill) are what block DDL, so keep them bounded. A failed deploy leaves the old container serving.
- **Recipes have no tradeskill-class field in Census**; `bench` is not one-to-one with class. The class comes from recipe-book items (`typeinfo.name == "recipescroll"`, with `recipe_list` and `classes`), which `scripts/build_recipe_classes.py` turns into `recipes.recipe_classes`. A recipe taught by two classes' books belongs to both.
