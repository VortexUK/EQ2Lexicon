---
name: add-migration
description: Write a Postgres migration that is safe to apply during an overlapping deploy. Use for any schema change - new table, column, index, constraint or seed data - before creating a file under db/migrations.
---

# Add a migration

Migrations run at application startup, on the new container, while the old container is
still serving. A file that raises aborts startup; a file that waits on a lock stalls every
reader queued behind it. The rules below exist for those two reasons.

## Steps

1. **Number**: list `db/migrations/` and take the highest prefix plus one. Name the file
   `NNNN_<family>_<topic>.sql`, where family is the schema (`users`, `parses`, `census`,
   `zones`, `raids`, `items`, `spells`, `recipes`, `aas`, `classes`).
2. **Never edit an applied migration.** The ledger (`public.schema_migrations`) is keyed by
   filename with no checksum. An edit changes nothing in production while the tests, which
   rebuild from scratch, still pass. Add a new file instead.
3. **Header**: a family file starts with exactly these two lines, then unqualified DDL:

   ```sql
   create schema if not exists <family>;
   set search_path to <family>, public;
   ```

   Follow with a `--` comment block saying why the change exists. A file whose first line
   is `-- global` is the exception, for cross-schema work such as role lockdown.
4. **Idempotent statements only**, because a file can be retried after a lock timeout:
   `ADD COLUMN IF NOT EXISTS`, `CREATE [UNIQUE] INDEX IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`,
   `DROP ... IF EXISTS`, and a `DO $$ ... $$` guard for anything conditional (a constraint
   swap, an extension that may be unavailable).
5. **Types**: timestamps are `bigint` epoch seconds; ids are
   `bigint GENERATED ALWAYS AS IDENTITY`; server-scoped tables carry a `world` column.
6. **New tables enable row level security themselves** (`ALTER TABLE ... ENABLE ROW LEVEL SECURITY`),
   matching the lockdown migrations.
7. **Keep it lock-short.** Each file is one transaction. Anything that rewrites a table or
   builds a large index holds an exclusive lock while the old container reads.
   - `CREATE INDEX CONCURRENTLY` cannot run inside a transaction, so it cannot go in a
     migration. Build the index concurrently against production first, then ship a
     migration with the plain `CREATE INDEX IF NOT EXISTS`, which records a no-op.
     `db/migrations/0016_items_indexes.sql` is the worked example.
   - Backfills of many rows belong in a script or a background task, not the migration.
8. **Seeds**: reference rows go after the family's `-- seeds` marker convention. Check how
   the base file for that family does it before adding seed data elsewhere.
9. **Both shapes must work**: a fresh database runs every file in order; production runs
   only the new one against its current shape. Do not rely on anything an earlier file
   "will" create differently.

## Check

```bash
uv run --frozen pytest tests/test_pg_migrate.py -q     # header convention, idempotency, retry
uv run --frozen python scripts/tools/verify.py         # picks that test up for any db/migrations change
```

If the change needs new queries, they go in the module's `.sql` sidecar, not in Python
strings. Conventions in full: `.claude/rules/postgres.md`. Runner behaviour:
`backend/pg_migrate.py`.

## If a deploy fails after a migration

A failed deploy is harmless: the old container keeps serving. Check the connection
budget before suspecting locks. The session pooler caps clients, and overlapping
containers have exhausted it before (`docs/runbooks/deploy.md`).
