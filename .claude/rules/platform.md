---
paths:
  - "backend/server/config.py"
  - "backend/server/server_context.py"
  - "backend/server/api/server.py"
  - "backend/server/api/admin.py"
  - "backend/server/api/auth.py"
  - "backend/server/db/erasure.py"
  - "backend/server/db/site_settings.*"
  - "backend/server/db/servers.*"
  - "frontend/src/pages/admin/**"
  - "frontend/src/pages/PrivacyPage.tsx"
---

# Platform: per-server context, site settings, erasure

## Per-server architecture

A single deployment serves multiple EQ2 servers, each on its own subdomain (e.g. `varsoon.eq2lexicon.com`, `wuoshi.eq2lexicon.com`).

- **Middleware**: `backend/server/server_context.py` adds `ServerContextMiddleware` which reads the request `Host` header, resolves it to the matching row in the `servers` registry table, and stores it on a contextvar for the lifetime of the request. Non-prod environments also accept a `X-Server` header or `?server=` query-param override for testing.
- **Accessors**: all route code calls `current_world()` / `current_server()` (from `backend/server/server_context.py`) rather than the old fixed `WORLD` constant. The bot still uses `WORLD` directly (single-server).
- **Registry**: the `servers` table in the users schema maps subdomain → world name + per-server settings (`max_level`, `current_xpac`, `launch_dt`, `display_name`). The registry is loaded into memory at startup via `load_registry()` and re-read after admin edits.
- **Per-server data**: character claims, item-watch rows, and parses each carry a `world` column so records are scoped to the server they belong to. Parses are additionally attributed by the `logger_server` field sent by the ACT plugin.
- **Universal data**: users, roles, and officer approvals are shared across servers (Discord identity is not server-specific).
- **Frontend bootstrap**: `GET /api/server` returns the active server's settings plus a `servers` array for the subdomain switcher; the frontend reads this once on load.
- **Single login**: `SESSION_COOKIE_DOMAIN` is set to the parent domain (e.g. `.eq2lexicon.com`) so one Discord login covers all subdomains. Leave it unset in local dev.
- **Seeding**: `EQ2_WORLD`, `SERVER_MAX_LEVEL`, `SERVER_CURRENT_XPAC`, and `LAUNCH_DT` env vars only seed the default server row on first migration; thereafter the registry is the source of truth and values are admin-editable per server.

## Architecture notes

- **Single env config**: `backend/server/config.py` exports `SERVICE_ID` and `WORLD`; web routes use `current_world()` from `backend/server/server_context.py` for the active per-request world (the bot still reads `WORLD` directly).

## Files

| File | Purpose |
|---|---|
| `backend/server/config.py` | Single source of truth: SERVICE_ID, WORLD from env vars |
| `backend/server/server_context.py` | Host → active-server middleware + contextvar accessors (`current_world()`, `current_server()`) + in-memory registry loaded from the `servers` table |
| `backend/server/api/server.py` | `GET /api/server` — bootstraps the frontend with the active server's world, display name, max_level, current_xpac, launch_dt, and the full public server list |
| `backend/server/db/erasure.py` | Right-to-erasure (privacy policy §8, 2026-09-28): `erase_user_sync(discord_id)` — ONE Postgres transaction spanning the users and parses schemas (search_path switched mid-transaction; users FKs deferred), so a crash can never leave a half-erased account — rows that ARE the person are deleted (users, tokens, roles, requests, claims, favourites, downloads, AA plans, availability, voice observations, tamper reports), `*_by` author columns are tombstoned to the placeholder user `deleted`, uploads stay as guild records with `source_dsn` → `plugin:deleted` and `hidden_by` cleared. Routes: admin `DELETE /api/admin/users/{id}` (not self) and self-service `DELETE /api/auth/me` (body `{confirm: <discord username>}`, clears the session); both audit `user_erased` and clear the claim cache, supporters cache and metrics last-seen map. Privacy page `frontend/src/pages/PrivacyPage.tsx` at `/privacy` is the one route rendered without a login (`PUBLIC_PATHS` + `PublicShell` in App.tsx) — keep its text in step with the code. Companion retention: `VOICE_OBSERVATION_RETENTION_DAYS` (90) swept in `app.py:_parse_cleanup_loop`; `/api/supporters` is session-gated and returns display names; fonts are self-hosted from `frontend/public/fonts` (`src/fonts.css`, regenerate with `scripts/dev/selfhost_fonts.py`) so no visitor IP reaches Google. |
| `backend/server/db/site_settings.py` | Site-wide (not per-server) key/value settings in the users schema (`site_settings`), facade aliases `get_site_setting` / `set_site_setting` / `all_site_settings`; absent row == unset. Only key today: `discord_invite_url` — admin `GET`/`PUT /api/admin/site-settings` (regex-validated `https://discord.gg/<code>` or `https://discord.com/invite/<code>`, audited `site_settings_updated`), delivered to the frontend in the `GET /api/server` bootstrap as `discord_invite_url` → `useServer().discordInviteUrl` → `components/DiscordCommunityLink.tsx` (icon in the header's top-right cluster + button on the Support page; renders nothing when unset). Admin UI: `pages/admin/SiteSettingsSection.tsx`. The invite must be made permanent in Discord (Expire after: Never, Max uses: No limit); the site never validates it live. |
