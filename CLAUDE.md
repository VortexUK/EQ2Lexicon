# CLAUDE.md — EQ2Lexicon

## What this project is

A web companion site for EverQuest 2 (TLE) with a Discord bot for spot checks. FastAPI backend, React/TypeScript frontend, Supabase Postgres, Daybreak Census API upstream. The web site is the primary product: character sheets, spell/AA tabs, item tooltips, parses and rankings, raid strategies, item watch, recipes, guild pages. One deployment serves several EQ2 servers, each on its own subdomain. Deployed on Railway; a push to `main` redeploys.

## This file stays small

It is loaded into every session, so it holds only what is true for every task. Detail lives where it loads on demand:

| Kind of knowledge | Where it goes |
|---|---|
| What one module does, its quirks | That module's docstring |
| Conventions for an area, cross-file invariants | `.claude/rules/<area>.md` (path-scoped: loads when a matching file is read) |
| A procedure with steps | `.claude/skills/<name>/SKILL.md`, or `docs/runbooks/` for operator runbooks |
| How a subsystem works, and why | `docs/architecture/`, `docs/decisions/` |

When you add a feature, document it in one of those places; do not add a row here. `tests/test_agent_docs.py` fails if this file passes 200 lines or 8 KB, or if a path named here, in a rule or in a skill does not exist.

## Map

| Path | What lives there |
|---|---|
| `main.py` | Process entry: runs migrations, then the web app and the bot |
| `backend/server/app.py` | FastAPI app: lifespan (pools, background loops), middleware, the `_ROUTERS` list |
| `backend/server/api/` | HTTP routes, one module or package per feature, all mounted under `/api` |
| `backend/server/db/` | Users-schema stores (`XStore(PgStoreBase)`) and the facade in `__init__.py` |
| `backend/server/parses/` | Parse storage, fights (mirror groups), retention, plausibility |
| `backend/server/core/` | Cross-cutting: audit log, throttles, middleware, moderation, leader lease |
| `backend/server/server_context.py` | Host to active server; `current_world()` / `current_server()` |
| `backend/census/` | Census HTTP client, models, item parsing, the persistent census store |
| `backend/eq2db/` | Reference-data catalogues: items, spells, recipes, zones, raids, aas, classes |
| `backend/image/` | PIL renderers for item tooltips and AA trees |
| `backend/bot/` | Discord bot: cogs are thin adapters over `render.py` + `messaging.py` |
| `backend/pg.py`, `backend/pg_migrate.py`, `backend/sql_loader.py`, `backend/db_catalogue.py` | Connection pools, migration runner, SQL sidecar loader, store and catalogue base classes |
| `db/migrations/` | All DDL, one schema per data family |
| `frontend/src/` | React app: `pages/`, `components/` (`ui/` primitives), `hooks/`, `lib/` |
| `scripts/` | Catalogue downloads, builds, previews; `scripts/tools/` holds the dev helpers named below |
| `tests/` | pytest suite (local PostgreSQL 17) mirroring the backend; frontend tests are colocated `*.test.ts(x)` |
| `docs/` | `architecture/`, `decisions/`, `runbooks/` |

## Invariants (every task)

- **World scoping.** One process serves every subdomain. Web code gets the active server from `current_world()` / `current_server()`; never use the `WORLD` constant in a route. Every server-scoped row, cache key and background job carries `world`. Users, roles and officer approvals are shared across servers. The bot resolves its world per Discord guild in `backend/bot/guild_context.py`.
- **Postgres only.** Every data family is a schema in one Supabase database; there is no SQLite. DDL goes in a new `db/migrations/NNNN_*.sql`; never edit an applied migration. SQL stays unqualified and lives in `.sql` sidecars; connections come from `backend/pg.py`.
- **Census is unreliable.** Read paths serve from the census store or cache and never block on Census; refreshes run in the background. A sparse refresh never nulls good data.
- **Uploads are untrusted.** Parse ingest validates the HMAC signature and plausibility, and never awaits Census before responding.
- **Logging.** `_log = logging.getLogger(__name__)`, lazy `%s` formatting, a `[component]` prefix, `audit_log(...)` for audit events. Tokens, signatures and secrets never appear in a log call. Before adding a per-request line, ask what it looks like at 3,600 an hour.
- **Frontend.** Tailwind v4 utilities for static styling, `style={{}}` only for runtime values; use the `ui/` primitives and shared hooks before writing your own.
- **Environment variables.** Read `.env.example` and `docs/runbooks/deploy.md` before adding or assuming one. `ENV` must be a dev value for the `X-Server` / `?server=` tenant override to work locally.
- **This repo is public.** No secrets, DSNs, tokens or personal identities in committed files, including rules and skills.

## Working here

- Verify before claiming done: `uv run --frozen python scripts/tools/verify.py` (changed files only; `--all` for the full pre-push gate). It prints failures only.
- Locate code with `uv run --frozen python scripts/tools/codemap.py <routes|stores|sql|pages|loops|tables> [filter]` before grepping.
- Tests need a local PostgreSQL 17 (`TEST_DATABASE_URL`); the suite provisions its own database and refuses non-local hosts.
- Dev servers: `scripts/dev_backend.ps1`, `scripts/dev_frontend.ps1`. Fake parse data: `scripts/dev/seed_fake_parses.py --character X` (`--wipe` removes it).
- Do not push until the user confirms local testing passes. Hold frontend and styling commits until the user has looked at them.
- A deploy is a migration run, and deploys overlap: keep migrations idempotent and lock-short.

## Rules (load automatically when a matching file is read)

| Rule | Covers |
|---|---|
| `.claude/rules/frontend.md` | Tailwind v4 rules, `ui/` primitives, hooks, formatters, design tokens, file-split and testing rules, design principles |
| `.claude/rules/backend.md` | Logging conventions in full |
| `.claude/rules/postgres.md` | Migrations, sidecars, stores, the catalogue convention, the eq2db catalogues |
| `.claude/rules/census.md` | Census API patterns, store, SWR cache, health, refresh, stats |
| `.claude/rules/parses.md` | ACT ingest (HMAC, gzip, zero-Census path), fights, retention, export API |
| `.claude/rules/guild.md` | Officer ranks, guild settings, recruitment, raid schedule, raiding-live, moderation |
| `.claude/rules/character.md` | AA endpoints, AA planner, gear sets, character rankings tab |
| `.claude/rules/platform.md` | Per-server architecture, server registry, site settings, erasure |
| `.claude/rules/rendering.md` | Tooltip and AA tree renderers |
| `.claude/rules/bot.md`, `.claude/rules/spellcheck.md` | Bot cogs; guild and spellcheck output rules, the spell blocklist |

If a task concerns an area and no file in it has been read yet, read that rule directly.

## Skills

| Skill | Use it to |
|---|---|
| `verify` | Run the right checks for what changed |
| `codemap` | Find a route, store, SQL block, page, table or background loop |
| `add-endpoint`, `add-migration`, `add-page` | Follow the fixed multi-file checklists |
| `prod-logs` | Read Railway logs, filtered and collapsed |
| `census-query` | Run one narrow Census query |
| `ship` | Commit, push and check the deploy |
| `ops` (user-invoked) | Runbook index: catalogue refresh, zones and raid seed, backup restore, deploy |
| `architecture-review` (user-invoked) | Staff-level architecture and production-readiness review |

## Docs

- `docs/architecture/`: parse ingest and grouping, rankings, upload integrity, auth and roles, census client, server registry, attendance, Discord bot, logging, SQL loader, raid strategies, rotation simulator.
- `docs/runbooks/`: `deploy.md` (deployment and environment variables), `backup-restore.md`, `catalogue-refresh.md`, `zones-and-raids-seed.md`, `raid-strategy-agent-polish.md`.
- `scripts/README.md`: every script with its purpose.
