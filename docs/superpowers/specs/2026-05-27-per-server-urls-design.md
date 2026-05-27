# Per-Server URLs — Design

**Date:** 2026-05-27
**Status:** Design approved, pending implementation plan

## Problem

EQ2Lexicon is hard-bound at startup to a single EQ2 server via env vars
(`EQ2_WORLD`, `SERVER_MAX_LEVEL`, `SERVER_CURRENT_XPAC`). We now serve two
servers — **varsoon.eq2lexicon.com** and **wuoshi.eq2lexicon.com** — and want the
**subdomain** to select the active server instead of an env var.

Constraints (from the request):
- **Users / auth / roles are universal** — one login works on both subdomains; the
  `contributor` role and access approvals span both.
- **Characters, character claims, parses, and leaderboards are per-server** — a
  user sees different characters (and a different primary character), and
  different parses/rankings, depending on the subdomain.
- **Admin**: per-server *Server Max Level* and *Current XPAC* (and *launch date*)
  become admin-panel settings instead of env vars; **character claims** review and
  **parse admin** are per-server, while **user approvals + role grants** stay
  universal.

## Goal

A **single deployment** serves both subdomains. Each request resolves its **active
server** from the `Host` header; all server-specific reads/writes scope to that
server's `world`, while universal data (users, roles, reference data) is shared.
Per-server settings move from env vars into an admin-editable registry.

## Approved decisions

| Decision | Choice |
|---|---|
| Deployment model | **Single deployment, Host-based** request resolution (not two deployments). |
| Request → server plumbing | **ASGI middleware** resolves `Host` → active server, stores it in a **request-scoped `contextvar`**; a `current_server()` / `current_world()` accessor replaces the module-level `_WORLD` imports at their use sites. |
| Per-server settings | **Max Level**, **Current XPAC**, **Server launch date** — stored per-server, admin-editable. Census **`service_id` stays a single global env var** (one Census API; only `world` differs). |
| Parse attribution | The plugin's **`logger_server`** (authoritative) → mapped to a known world; fall back to the request's active world when absent. |
| Existing data | Production data is all **Varsoon** today → backfill existing per-server rows to `Varsoon`. |
| Cross-subdomain login | Session cookie scoped to the **parent domain** via a new `SESSION_COOKIE_DOMAIN` env (`.eq2lexicon.com` in prod; unset in dev). |

## Current state (what we're changing)

- `census/config.py` defines `SERVICE_ID`, `WORLD` (`EQ2_WORLD`, default `Varsoon`),
  `SERVER_MAX_LEVEL`. `SERVER_CURRENT_XPAC` is read as a raw env var in `aa.py`
  and `rankings.py`. `WORLD` is imported by ~32 modules.
- `CensusClient` already takes `world` **per call**; only `service_id` is
  per-client. So worlds are already parameterizable — routes simply always pass the
  env default today.
- `census/census_store.py` (characters, guilds) is **already `(name_lower, world)`-keyed** — no schema change needed.
- Users DB (`web/db.py`): `users`, `user_roles`, `role_requests`, `api_tokens` are
  world-agnostic; **`character_claims` and `item_watch` have no world column**.
- Parses DB (`parses/db.py`): `encounters` + `combatants` have **no world column**;
  `act_encid` is globally unique. Ingest (`web/routes/parses.py`) already reads
  `logger_server` but does **not persist** it.
- Frontend uses **relative (same-origin) URLs** — it already works under any
  subdomain; there is no server-config endpoint and no subdomain-aware logic.

## Architecture

### 1. Server resolution (request → active server)

A new ASGI middleware (`web/server_context.py`) resolves the active server once per
request:

1. Read the `Host` header; take the leading subdomain label (`varsoon` from
   `varsoon.eq2lexicon.com`).
2. Look it up in the **servers registry** (section 2, cached in memory) → the
   active `Server` (world + settings).
3. Store it in a **`contextvar`** (`_active_server`). Expose accessors:
   - `current_server() -> Server`
   - `current_world() -> str` (the server's `world`)

Resolution fallbacks (in order): exact subdomain match → if the host is unknown,
missing, an apex/IP, or a health-check → the **default server** (the `EQ2_WORLD`
value, i.e. `Varsoon`). In **non-production only**, an `X-Server` header or
`?server=` query param overrides resolution (for local testing of Wuoshi).

`current_world()` replaces every module-level `_WORLD` usage at its call site
(Census calls, cache keys, store lookups). Background tasks created **within** a
request (e.g. the census-refresh tasks) inherit the contextvar because
`asyncio.create_task` copies the current context. Tasks that run **outside** any
request — the census health poll (world-agnostic) and the startup pre-warm — do not
rely on the contextvar; pre-warm iterates the registry explicitly.

**Why contextvar over `Depends`/`request.state`:** the per-server code today reads a
module-level `_WORLD`; a contextvar accessor swaps `_WORLD` → `current_world()`
in place without threading a `world` argument through every cache/refresh/store
helper that isn't request-aware. `Depends(get_server)` remains available for route
handlers that want the full `Server` object (settings).

### 2. Servers registry + per-server settings

New table in the **universal users DB** (`web/db.py`):

```sql
CREATE TABLE IF NOT EXISTS servers (
    world          TEXT PRIMARY KEY,        -- canonical Census world, e.g. 'Varsoon'
    subdomain      TEXT NOT NULL UNIQUE,    -- 'varsoon', 'wuoshi'
    display_name   TEXT NOT NULL,           -- 'Varsoon'
    max_level      INTEGER NOT NULL,        -- was SERVER_MAX_LEVEL
    current_xpac   TEXT,                     -- was SERVER_CURRENT_XPAC (full name)
    launch_dt      TEXT,                     -- ISO 8601, was LAUNCH_DT_ISO; nullable
    updated_at     INTEGER NOT NULL
);
```

Seeded on migration with **Varsoon** and **Wuoshi** rows (Varsoon's values taken
from the current env vars; Wuoshi's set to sensible defaults / edited in admin).

- Loaded into an in-memory registry on startup; refreshed on edit (settings change
  rarely). The registry maps both `subdomain → Server` and `world → Server`.
- **Replaces env consumption**: `rankings.py` (default expansion + kill
  qualification) and `aa.py` (`/aa/config` xpac + AA cap) read
  `current_server().current_xpac` / `.max_level`; the launch timer reads
  `.launch_dt`. The `SERVER_MAX_LEVEL` / `SERVER_CURRENT_XPAC` / launch-date env
  vars are retired (or used only to seed the migration).
- `EQ2_WORLD` remains as the **default-server** selector for fallback/dev; Census
  `CENSUS_SERVICE_ID` remains global.

### 3. Per-server data scoping (schema)

| Table | Change | Backfill |
|---|---|---|
| `census_store.characters`, `.guilds` | none — already `(name_lower, world)` | n/a |
| `character_claims` | add `world TEXT NOT NULL`; claims + primary-character become per `(discord_id, world)` | `Varsoon` |
| `item_watch` | add `world TEXT NOT NULL`; unique `(world, guild_name, character_name, item_id)` | `Varsoon` |
| `encounters` | add `world TEXT NOT NULL`; uniqueness + ingest idempotency become `(world, act_encid)` | `Varsoon` |
| `combatants` | no column — scoped via parent `encounter_id` | n/a |
| users, user_roles, role_requests, api_tokens | none — universal | n/a |
| reference data (items, recipes, zones, strategies, classes) | none — universal | n/a |

Migrations are idempotent (follow the established `_MIGRATIONS` pattern: guard each
`ALTER`/index with try/except so re-runs are no-ops). For SQLite, adding a
`NOT NULL` column to an existing table requires a default for the backfill — add the
column with a default of `'Varsoon'` (or add nullable, `UPDATE ... SET world =
'Varsoon' WHERE world IS NULL`, then rely on app-level always-set). Unique
constraints that change (`item_watch`, `encounters`) are rebuilt via the standard
SQLite "create new table + copy + swap" migration if needed, or a new unique index
if the old constraint can coexist.

### 4. Auth, roles, admin

- **Universal login**: set the session cookie's `domain` from `SESSION_COOKIE_DOMAIN`
  (e.g. `.eq2lexicon.com`) so a session created on one subdomain is sent on the
  other. Unset in dev (host-only cookie on `localhost`). The Discord OAuth callback
  works unchanged; the shared-domain cookie makes the resulting session span both
  subdomains.
- **Universal**: `users`, `user_roles` (`contributor`), `role_requests`,
  `access_status` approvals — unchanged, visible/editable from either subdomain.
- **Per-server admin** (admin identity stays global via `ADMIN_DISCORD_IDS`):
  - **Claim review** (`web/routes/guild_officer.py` / admin claims) filters to
    `current_world()`.
  - **Parse admin** (delete/list in `web/routes/admin.py` + parse endpoints) filters
    to `current_world()`.
  - **Per-server settings editor**: new admin endpoint(s) to read/update a server's
    `max_level` / `current_xpac` / `launch_dt` (writes the `servers` row, refreshes
    the registry).
  - **Role/access approvals** remain universal in the admin panel.

### 5. Parses

- **Attribution**: at ingest, resolve the parse's world from `logger_server`
  (authoritative; the same sanitize/resolve already used for guild lookup), mapped
  to a known registry world; fall back to `current_world()` when `logger_server` is
  absent/unknown (older plugins / the local-ingest path). Persist it on the
  `encounters.world` column.
- **Idempotency**: dedupe on `(world, act_encid)` so the same ACT encounter id from
  two servers cannot collide.
- **Reads**: parse listing, encounter detail, leaderboards, and rankings filter by
  `current_world()`; ranking kill-qualification uses the active server's `max_level`.

### 6. Frontend

- Already same-origin → character main screen / primary char, parses, leaderboards
  auto-scope to the subdomain via the backend.
- New **`GET /api/server`** returns the active server for the current subdomain:
  `{ world, display_name, max_level, current_xpac, launch_dt }`. The frontend uses it
  for: the launch-countdown timer (replaces the build-time env), AA-cap display, a
  **server name in the header**, and a **"switch server"** link to the other
  subdomain (built from the registry's subdomains).
- No change to the relative-URL API client; all per-server scoping happens
  server-side from the Host.

### 7. Migration & dev

- One migration pass: add `world` columns + backfill existing rows to `Varsoon`;
  rebuild changed unique constraints; create + seed the `servers` table from current
  env values (Varsoon) + a Wuoshi row.
- **Dev**: no matching subdomain on `localhost` → fall back to `EQ2_WORLD`
  (`Varsoon`). The non-prod `X-Server` / `?server=` override exercises Wuoshi
  locally. `SESSION_COOKIE_DOMAIN` unset in dev.
- The DB files remain gitignored / hand-managed per existing convention; these are
  schema migrations on existing DBs (no data loss — additive columns + backfill).

## Components / file map

- **New** `web/server_context.py` — the `Server` dataclass, the in-memory registry
  (load/refresh from the `servers` table), the resolution middleware, and the
  `current_server()` / `current_world()` contextvar accessors + `Depends(get_server)`.
- **New** servers table + helpers in `web/db.py` (`get_server_by_subdomain`,
  `get_server_by_world`, `list_servers`, `upsert_server`).
- **Modified** `web/app.py` — register the middleware (early in the stack); set the
  session cookie `domain` from `SESSION_COOKIE_DOMAIN`; seed the registry on startup;
  pre-warm iterates servers.
- **Modified** route modules that read `_WORLD` → `current_world()`:
  `character.py`, `characters.py`, `guild.py`, `guild_officer.py`, `aa.py`,
  `parses.py`, `rankings.py`, `claim.py`, `item_watch.py`, `notifications.py`,
  `census_refresh.py` (the refresh orchestrator's `_WORLD`).
- **Modified** `web/db.py` — `character_claims` + `item_watch` migrations (add
  `world`), per-`(discord_id, world)` claim/primary-char queries, world-scoped
  item-watch queries.
- **Modified** `parses/db.py` + `web/routes/parses.py` — `encounters.world` column,
  `(world, act_encid)` idempotency, attribution from `logger_server`, world-scoped
  reads.
- **Modified** `web/routes/admin.py` — per-server claim/parse scoping; per-server
  settings endpoints.
- **Modified** `web/routes/rankings.py`, `web/routes/aa.py` — read settings from the
  active server instead of env vars.
- **New** `GET /api/server` (small route, e.g. in `web/server_context.py`'s router
  or a `web/routes/server.py`).
- **Modified** frontend: a `useServer()` hook/context fetching `/api/server`; header
  server name + switcher; launch timer + AA-cap display read from it.
- **Bot note**: `bot/` cogs still use the env `WORLD`/`SERVICE_ID` for the single
  configured server — **out of scope** here (the Discord bot remains single-server;
  only the web companion becomes multi-server). Documented, not changed.

## Edge cases

| Situation | Behaviour |
|---|---|
| Unknown / apex / IP Host, health check | Default server (`EQ2_WORLD` = Varsoon). |
| ACT ingest POST to a fixed URL | Parse attributed by `logger_server`, not the Host. |
| `logger_server` missing (old plugin) | Fall back to the request's active world. |
| Same `act_encid` from both servers | Distinct rows — idempotency is `(world, act_encid)`. |
| Same character/guild name on both servers | Distinct — `census_store` keyed by `(name, world)`; claims/parses scoped by world. |
| User claims a character on each server | Two `character_claims` rows, one per `(discord_id, world)`; independent primary characters. |
| Login on varsoon.\*, visit wuoshi.\* | Same session (parent-domain cookie); same roles/approvals; per-server characters/claims/parses. |
| Local dev (`localhost`) | Default server; optional `?server=`/`X-Server` override (non-prod only). |

## Out of scope

- The **Discord bot** (`bot/`) stays single-server (its own `EQ2_WORLD`).
- Adding a **third+ server** (the design supports it via new `servers` rows + a DNS
  subdomain, but only Varsoon/Wuoshi are configured now).
- Any change to reference data (items/recipes/zones/strategies) — universal.
- Migrating away from `EQ2_WORLD` entirely — it stays as the default-server selector.

## Environment variables

| Variable | Change |
|---|---|
| `EQ2_WORLD` | Now the **default-server** selector (fallback / dev), not the only server. |
| `SERVER_MAX_LEVEL` | **Retired** from runtime use → per-server `servers.max_level` (used once to seed the Varsoon row). |
| `SERVER_CURRENT_XPAC` | **Retired** from runtime use → per-server `servers.current_xpac` (seeds Varsoon). |
| launch-date env (`LAUNCH_DT_ISO`) | **Retired** from runtime use → per-server `servers.launch_dt` (seeds Varsoon). |
| `CENSUS_SERVICE_ID` | Unchanged — single global Census key. |
| `SESSION_COOKIE_DOMAIN` | **New** — parent domain for the session cookie (`.eq2lexicon.com` in prod; unset in dev) so one login spans both subdomains. |

## Testing

- **Resolution**: subdomain → server; unknown/apex/missing host → default;
  non-prod `X-Server`/`?server=` override; contextvar set/reset per request.
- **Settings registry**: `rankings.py` + `aa.py` read per-server `max_level` /
  `current_xpac` (not env); admin edit updates the row + refreshes the registry.
- **Per-server isolation**: claims, item-watch, and parses created under one world
  are invisible under the other; primary character is per `(user, world)`.
- **Parses**: `logger_server` attribution maps to the right world; `(world,
  act_encid)` idempotency prevents cross-server collision; leaderboards/rankings
  filter by active world and qualify against per-server `max_level`.
- **Auth**: a session created with `SESSION_COOKIE_DOMAIN` set is accepted on both
  subdomains; roles/approvals are shared; admin claim/parse views scope to the
  active server.
- **Migration**: idempotent re-run; existing rows backfilled to `Varsoon`; servers
  table seeded with both servers.
- **Frontend**: `/api/server` returns the active server; header shows the server name
  + a working switch link; launch timer + AA caps reflect the active server.

## Rollout

Additive + backfill — no data loss. Steps:
1. Deploy migrations (add columns, backfill `Varsoon`, seed `servers`).
2. Set `SESSION_COOKIE_DOMAIN=.eq2lexicon.com` and confirm Railway serves both custom
   domains from the one deployment.
3. Verify resolution on each subdomain; confirm Varsoon behaves exactly as before and
   Wuoshi shows its own (initially empty) per-server data.
4. Admin sets Wuoshi's `max_level` / `current_xpac` / `launch_dt`.
