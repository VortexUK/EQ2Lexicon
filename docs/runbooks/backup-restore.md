# Backups and the restore drill

## Backups (Postgres → Cloudflare R2, nightly pg_dump)

Every data family lives in Supabase Postgres; `.github/workflows/pg-backup.yml` runs a nightly `pg_dump -Fc` of all ten family schemas (items/spells/recipes are not re-seedable without a multi-hour Census crawl) into the R2 bucket (`pgdump/` prefix) with 30-day in-action pruning. Secrets live on the GitHub repo: `SUPABASE_DB_URL` (session pooler) + the four `R2_*` values. Restore drill:

```bash
# target database first: CREATE EXTENSION IF NOT EXISTS pg_trgm;  (the dump
# covers schemas only — without it idx_items_name_trgm fails to build)
pg_restore -d "$DATABASE_URL" --clean --if-exists --no-owner --no-privileges -j 4 eq2lexicon-<ts>.dump
```

Drilled 2026-10-08: the 438 MB dump restored all ten schemas into a scratch local PG17 database in 45 s (`-j 4`); expect two ignorable errors ("schema public already exists", and the trgm index when the extension is missing). Static-table counts (items/spells/recipes/zones) must match prod exactly.

(The litestream/SQLite replication era is over; its R2 prefixes can be deleted ~2 weeks after a healthy cutover. The one-time R2 token how-to: dash.cloudflare.com → R2 → API tab → Create API Token — NOT "Account API tokens" — Object Read & Write on the bucket.)
