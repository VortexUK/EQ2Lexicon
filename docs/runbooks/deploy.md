# Deploy and environment

## Deployment

- Platform: Railway, Nixpacks builder
- Push to `main` branch triggers redeploy
- New slash commands may take up to 1 hour to propagate globally, but appear instantly in the registered guild IDs above
- Do not push until the user confirms local testing passes

## Environment variables

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token from Discord developer portal |
| `CENSUS_SERVICE_ID` | Census API service ID (default `example`, rate-limited) |
| `EQ2_WORLD` | Default-server selector — selects the `servers` registry row treated as the fallback when no subdomain matches. Also used directly by the bot. Seeds the Varsoon row on first migration; runtime value comes from the registry. |
| `ENV` | `production` on Railway (set 2026-10-07). Only a dev-style value (`dev`/`development`/`local`/`test`) enables the `X-Server` header / `?server=` tenant override in `server_context.py`; unset == override closed. Tests set `dev` in conftest. |
| `SESSION_COOKIE_DOMAIN` | Parent domain for the session cookie so one login spans both subdomains (e.g. `.eq2lexicon.com` in prod). Leave unset in dev. |
| `SERVER_CURRENT_XPAC` | Seed-only — runtime value is per-server in the `servers` table (admin-editable). Seeds the current expansion for the Varsoon row on first migration. |
| `SERVER_MAX_LEVEL` | Seed-only — runtime value is per-server in the `servers` table (admin-editable). Seeds the max character level for the Varsoon row on first migration. |
| `LAUNCH_DT` | Seed-only — runtime value is per-server in the `servers` table (admin-editable). Seeds the server launch datetime for the Varsoon row on first migration. |
| `ADMIN_DISCORD_IDS` | Comma-separated Discord IDs allowed to hit `/api/admin/*` and delete arbitrary parses |
| `DATABASE_URL` | Postgres DSN for the writable families (users/parses/census/zones/raids — one Supabase database, five schemas). Resolution chain: `DATABASE_URL` → `SUPABASE_DB_URL` → `POSTGRES_CONNECTION_STRING` (`backend/pg.py`). Production = the Supabase SESSION pooler (`:5432`, `sslmode=require`); never the transaction pooler (`:6543`). Migrations in `db/migrations/` apply at app startup. **Pooler size**: session mode gives every client its own server connection, and the pooler's pool size caps clients. One container holds up to 11 (async 5 + sync 5 + leader lease 1; idle connections close after `POOL_MAX_IDLE_S`=60 s) and deploys overlap, so the Supabase pool size must be ≥ ~30 (set to 40 on 2026-10-08 after the default 15 crash-looped a deploy with 'max clients reached in session mode'). `main.py` retries a full pooler for ~80 s at startup; a standby leader holds no connection. |
| `TEST_DATABASE_URL` | Local PostgreSQL 17 the pytest suite provisions and runs against (default `postgresql://postgres:postgres@localhost:5432/eq2lexicon_test`; created automatically). See `tests/fixtures/pg.py`. |
| `PARSE_RETENTION_DAYS` | Days after the fight before the retention sweep (`backend/server/parses/cleanup.py`) hard-deletes a trash parse and collapses a named fight's duplicate uploads to its primary. Default `3`. |
| `PARSE_DETAIL_RETENTION_RAID_DAYS` / `_DUNGEON_DAYS` / `_OTHER_DAYS` | Tiered breakdown-detail retention (defaults 30/14/7): days before a kept fight's `attack_types`+`damage_types` are dropped, by zone category (curated raid / curated group-instance / other). Summary rows live forever. |
| `INGEST_CLIENT_LIMITS` | Per-client-app flood protection on `/api/parses/ingest` (`backend/server/core/client_throttle.py`): `<User-Agent prefix>=<limit>` pairs, `;`-separated, matched case-insensitively as a prefix, bucketed per (app, uploader). Default `EQ2AdvancedDesktop=30/hour` — the third-party client that retried a 500 every ~2 s for nine hours on 2026-09-27. Unlisted clients unaffected; empty disables. |
| `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` | Twitch app (client-credentials) creds for the raid-schedule "Raiding live" list (`backend/server/raid_live.py`). Both optional — unset ⇒ live list disabled, raid schedule unaffected. |
| `DB_ITEMS_PATH` / `DB_SPELLS_PATH` / `DB_RECIPES_PATH` | **Retired** (Phases 2-3, 2026-10) — every data family lives in a Postgres schema (`DATABASE_URL`); the Railway volume is gone and no `DB_*_PATH` var does anything (`DB_AAS_PATH` included — aas/classes are migration-seeded, 0011/0012). Catalogue refreshes are script runs (`scripts/download_*.py`, `scripts/build_aas_db.py`) against the DB. |
| `DB_CLASSES_PATH` | **Ignored** (warning logged if set) — class data lives in the Postgres `classes` schema, seeded by `db/migrations/0011_classes.sql` (Phase 3). The seeds are canonical and carry `census_classid` separately from `icon_id` (the 2026-08 Coercer/Illusionist prod bug was that conflation). |
| `R2_ENDPOINT` / `R2_BUCKET` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | Cloudflare R2 bucket for the nightly `pg_dump` backups — set as GITHUB ACTIONS secrets (`.github/workflows/pg-backup.yml`), not Railway vars (litestream is retired). |

## What triggers a deploy

`watchPatterns` under `[build]` in `railway.toml` decides which paths redeploy the service (gitignore-style, matched from the repo root; a negation needs a preceding `**` include). `ops/`, `docs/`, `.github/`, `.claude/` and root Markdown files are excluded. Confirm against Railway's config-as-code reference before telling anyone a push will or will not deploy. A failed deploy leaves the previous container serving.
