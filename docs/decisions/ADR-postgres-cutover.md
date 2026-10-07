# ADR: SQLite to Postgres cutover

Status: accepted, executed.

## Context

The backend originally kept each data family (users, parses, census, zones, raids, and the
read-only items / spells / recipes catalogues) in its own SQLite file on a Railway volume,
replicated to R2 by litestream. Everything now lives in one Supabase Postgres database, one
schema per family, with DDL owned by `db/migrations/NNNN_<family>.sql` and applied by
`backend/pg_migrate.py` at app startup.

Moving the data needed a copy tool that could be rehearsed safely against production-sized
data, that tolerated the integrity gaps SQLite had allowed (the users schema never enforced its
foreign keys), and that would not re-import parse breakdown detail the new retention policy
would immediately delete.

## Decision

`scripts/migrate_to_postgres.py` is a re-runnable, whole-family copy:

- **Idempotent by truncation.** Every run `TRUNCATE ... RESTART IDENTITY CASCADE`s the target
  family, then reloads it. Rehearsals therefore are the real run, and the final pass in the
  cutover window is just a re-run against the latest restore of the SQLite files.
- **Schema-driven.** Column lists and types are introspected from `information_schema`, so the
  copy follows the migration-defined Postgres schemas rather than a hand-maintained mapping.
  Columns that exist only in Postgres are left at their defaults.
- **FK-ordered, committed per table.** Tables are copied parent-before-child (topological order
  over the schema's foreign keys) with `COPY ... FROM STDIN`, and each table commits on its own.
  This bounds WAL growth (a family-wide transaction can fill a small Supabase disk mid-COPY);
  idempotency is unaffected because every run starts by truncating the family.
- **Family order matters.** Zones are loaded before parses, because the parses detail-retention
  tiers classify each encounter by its zone using the zones schema. With empty zones data
  every fight classifies as "other" and curated raid detail would be over-pruned.
- **Type transforms** are chosen by the Postgres column type: `integer[]` columns accept the
  SQLite comma-separated form (`"2,4"` becomes `{2,4}`); `jsonb` text is validated with
  `json.loads` and shipped as text for Postgres to parse; `bytea` passes through.
- **Detail retention applied at load.** For parses, `attack_types` / `damage_types` rows whose
  encounter is past its retention tier (`backend/server/parses/cleanup.py`) are not copied, and
  those encounters are stamped `detail_pruned_at`. This is what makes the parses footprint fit.
- **Orphans are quarantined, not loaded.** Each child table is anti-joined against its parent in
  SQLite first. Orphan rows are written to `reports/orphans_<schema>.<table>.jsonl` and excluded;
  any orphan fails the run unless `--allow-orphans` is passed.
- **Pre-flight audits.** Duplicate approved character claims per `(world, lower(name))` are
  reported and abort the run, because the Postgres partial unique index would reject them
  mid-load.
- **Post-load fix-ups.** Every identity sequence is `setval`'d to `MAX(col) + 1` (without this,
  the first live insert collides on the primary key), and every table is `ANALYZE`d so the first
  production queries do not plan against empty statistics.
- **Verification.** Per table, the Postgres row count must equal the rows copied (a hard
  failure otherwise). A JSON report lands in `reports/migrate_report.json`.
- **Safety.** The target host is printed and the run needs `--yes` or an interactive "yes".
  The bulk-load session sets `statement_timeout = 0`, because the server-side timeout would
  otherwise kill a `TRUNCATE` queued behind live application locks.

The reference families `aas` and `classes` are not copied: migrations 0011 and 0012 seed their
full data, so every environment is data-complete from migrations alone.

Usage:

    uv run python scripts/migrate_to_postgres.py --users <users.db> --parses <parses.db> \
        --census <census.db> --zones <zones.db> --raids <raids.db> [--dsn ...] [--yes]

## Consequences

- There is no SQLite in the backend. `migrate_to_postgres.py` is the only remaining `sqlite3`
  user and is useful only while SQLite source files exist.
- The copy must not race a running application against the same database: startup background
  jobs (for example the parse cleanup sweep) can delete rows a COPY is about to reference, and
  live ingest takes locks a `TRUNCATE` must wait for. Stop or isolate the app during the final
  pass.
- Parse breakdown detail older than its tier did not survive the move; summary rows
  (encounters, combatants) did.

## Rollback

While the SQLite source files and the pre-cutover deployment still exist, rollback is: redeploy
the pre-cutover Railway deployment and flip the environment back to the SQLite paths. Anything
written to Postgres after the cutover is lost in that case. Once the volume is deleted, recovery
is a `pg_restore` of a nightly dump instead (see Backups in CLAUDE.md).
