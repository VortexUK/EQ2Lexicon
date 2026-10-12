# Backups and the restore drill

## Backups (Postgres → Cloudflare R2, nightly pg_dump)

Every data family lives in Supabase Postgres; `.github/workflows/pg-backup.yml` dumps it with `pg_dump -Fc` into the R2 bucket (`pgdump/` prefix). Secrets live on the GitHub repo: `SUPABASE_DB_URL` (session pooler) + the four `R2_*` values.

There are two dumps, because a dump is billed Supabase egress and the item catalogue is about three quarters of the bytes while hardly changing (2026-10-12; until then one nightly dump carried all ten schemas):

| Dump | Schemas | When | Kept |
|---|---|---|---|
| `eq2lexicon-<ts>.dump` | every family schema except `items` | nightly, 04:17 UTC | 30 days |
| `eq2lexicon-items-<ts>.dump` | `items` alone | the 1st of the month, 04:47 UTC | 100 days |

Dumps taken before 2026-10-13 are the old single kind and include `items`. Run the items dump by hand (`gh workflow run pg-backup.yml -f what=items`) after a catalogue refresh, since nothing else captures one until the 1st. An item the site cached after the last items dump is not in it; it is fetched from Census again on first use.

Restore drill, the newest dump of each kind (items/spells/recipes are not re-seedable without a multi-hour Census crawl):

```bash
# target database first: CREATE EXTENSION IF NOT EXISTS pg_trgm;  (the dump
# covers schemas only — without it idx_items_name_trgm fails to build)
pg_restore -d "$DATABASE_URL" --clean --if-exists --no-owner --no-privileges -j 4 eq2lexicon-<ts>.dump
pg_restore -d "$DATABASE_URL" --clean --if-exists --no-owner --no-privileges -j 4 eq2lexicon-items-<ts>.dump
```

Drilled 2026-10-08: the 438 MB dump restored all ten schemas into a scratch local PG17 database in 45 s (`-j 4`); expect two ignorable errors ("schema public already exists", and the trgm index when the extension is missing). Static-table counts (items/spells/recipes/zones) must match prod exactly.

(The litestream/SQLite replication era is over; its R2 prefixes can be deleted ~2 weeks after a healthy cutover. The one-time R2 token how-to: dash.cloudflare.com → R2 → API tab → Create API Token — NOT "Account API tokens" — Object Read & Write on the bucket.)
