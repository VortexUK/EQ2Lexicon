# CLAUDE.md — EQ2Lexicon

## What this project is

A web companion site for EverQuest 2 (TLE), with a Discord bot for spot checks (FastAPI + React/TypeScript). Queries the Daybreak Census API. The web site is the primary product: character sheets, spell/AA tabs, item tooltips, parses + rankings, raid strategies, item watch, recipes, multi-server subdomain support. The bot provides /item, /guild, /spellcheck, /aacheck. Deployed on Railway; git push to `main` triggers redeploy.

## Key files

| File | Purpose |
|---|---|
| `backend/census/client.py` | All Census API HTTP calls. `CensusClient` has `get_item`, `get_guild`, `get_character_spells`, `get_character_aas`, `get_raw_item`. |
| `backend/census/models.py` | Dataclasses: `ItemData`, `ItemStat`, `ItemEffect`, `GuildData`, `GuildMember`, `CharacterSpells`, `SpellEntry`, `NodeAA`, `CharacterAAs` |
| `backend/census/constants.py` | `STAT_MAP` (stat display names/groups), class frozensets (`FIGHTERS`, `PRIESTS`, `SCOUTS`, `MAGES`, `ARTISANS`), `ARCHETYPES`, `CLASS_GROUPS`, `TYPEINFO_DISPLAY`, `ITEM_DISPLAY` |
| `backend/census/item_parser.py` | Item data parsing helpers (parse_item, parse_stats, parse_effects, parse_flags, etc.) extracted from client.py |
| `backend/eq2db/spells.py` | Local SQLite spell catalogue: strip_roman, unique_highest_entries, load_blocklist, find_by_ids, find_by_crc, spell_to_row, upsert_spells |
| `backend/eq2db/recipes.py` | Local SQLite recipe catalogue (~70k rows). Helpers: find_by_id, find_by_name, find_by_output_id. Secondary components stored as JSON array. |
| `backend/eq2db/zones.py` | Local SQLite zone catalogue (~1124 rows). Tables: `zones`, `zone_types`, `zone_aliases`, `zone_encounters`, `zone_encounter_mobs`. Lookup helpers: `find_by_name`, `list_by_expansion`, `list_by_event`, `list_by_type`, `list_bosses_for_zone`, `find_zones_by_boss`. Zone metadata from `scripts/dev/eq2_zones.cleaned.json`; rebuild via `scripts/build_zones_db.py`. Boss data is curator-managed in-place. |
| `backend/census/wikitext_md.py` | MediaWiki wikitext → markdown converter (mwparserfromhell). Handles EQ2i templates, wikilinks, nested lists, headings, bold/italic. Used by the raid scraper. |
| `backend/eq2db/raids.py` | Local SQLite raid-strategy catalogue: `raid_zones` + `raid_encounters` (markdown blob per encounter) + `raid_encounter_revisions`. `DB_RAIDS_PATH` env var. |
| `backend/census/store.py` | Persistent SQLite store (characters + guilds keyed by name_lower + world). Keep-best-known merge — sparse Census refresh never nulls good data. `DB_CENSUS_PATH` env. |
| `backend/image/tooltip.py` | PIL renderer for item tooltips. Renders at 2× then downsamples (SCALE=2, ZOOM=1.3). See file for colour/stat/ordering details. |
| `backend/image/aa_tree.py` | AA tree renderers and coordinate systems. See file for tree-type detection and coordinate arithmetic. |
| `backend/bot/bot.py` | Registers all cogs, syncs slash commands to three specific guild IDs (648253204760625160, 955890381847928892, 1502314690041221260) for instant propagation plus a global sync. |
| `backend/bot/cogs/items.py` | `/item` — accepts name, numeric ID, or game link |
| `backend/bot/cogs/guild.py` | `/guild` — tabular member list sorted by rank then level |
| `backend/bot/cogs/spellcheck.py` | `/spellcheck` — spell tier summary or full list (`details:True`) |
| `backend/bot/cogs/aacheck.py` | `/aacheck` — renders a character's AA tree with tier badges |
| `backend/server/config.py` | Single source of truth: SERVICE_ID, WORLD from env vars |
| `backend/server/server_context.py` | Host → active-server middleware + contextvar accessors (`current_world()`, `current_server()`) + in-memory registry loaded from the `servers` table |
| `backend/server/api/server.py` | `GET /api/server` — bootstraps the frontend with the active server's world, display name, max_level, current_xpac, launch_dt, and the full public server list |
| `backend/server/cache.py` | TTLCache with stale-while-revalidate: character_cache, guild_cache, claim_cache. Character and guild read paths serve from `census_store` first and never block on Census. |
| `backend/server/api/aa.py` | GET /api/character/{name}/aas — AA profile list with per-tree data |
| `backend/server/api/characters.py` | GET /api/characters/search — character name search |
| `backend/server/api/guild_officer.py` | Officer claim-review endpoints; imports _officer_chars, _roster_rank_map from guild.py |
| `backend/server/api/item_watch.py` | Item watch endpoints; imports _officer_chars, _roster_rank_map from guild.py |
| `backend/server/census_health.py` | Site-wide Census availability signal: background poll every 5 min; `is_down()`/`get_state()` read by the read/refresh paths. |
| `backend/server/census_events.py` | In-process async pub/sub backing the SSE stream (single-process only). |
| `backend/server/census_refresh.py` | Background refresh orchestration (throttle 15 min / in-flight dedupe / skip-when-down); merges into census_store, updates cache, publishes SSE. `_merge_roster` best-known join. |
| `backend/server/api/census.py` | `GET /api/backend/census/health` (first-paint snapshot) + `GET /api/backend/census/stream` (SSE: character/guild refresh records + health changes). |
| `backend/server/parses/cleanup.py` | Parses retention sweep (`run_parse_cleanup`). Trash (non-boss) hard-deleted `PARSE_RETENTION_DAYS` after the fight; named (boss) fights keep only the primary (longest) upload after the same window, deleting duplicate uploads. Reuses `_group_into_fights` so it picks the same primary rankings link to (rankings-safe); never touches soft-deleted rows. Run periodically by `app.py:_parse_cleanup_loop`. |
| `backend/server/api/raid_schedule.py` | Guild raid-schedule API. Public `GET /api/guild/{name}/raid-schedule`; officer-or-admin `PUT` (full replace, ≤4 teams × ≤4 raids, each ≤5h, IANA tz, Twitch validated, free text blocklist-screened). Clearing == PUT with empty `teams`. `GET /api/raiding-live` returns the current world's live teams. Tables `raid_teams`/`raid_slots` in `users.db` (`backend/server/db/raid_schedule.py`). |
| `backend/server/raid_live.py` | Twitch-verified "Raiding live" poller (`poll_loop`, `app.py` lifespan). Finds teams inside a scheduled window (team tz, ±15min grace) then verifies live via Twitch Helix; caches per world for `/api/raiding-live`. No-ops without `TWITCH_CLIENT_ID/SECRET`. |
| `backend/server/core/twitch.py` | `parse_twitch_login` (accept only `twitch.tv/<channel>`) + `is_blocked` (thin wrapper over `text_moderation`). |
| `backend/server/core/text_moderation.py` | Shared profanity screen + input sanitiser for officer free text (raid team names/labels) and Twitch logins. `contains_blocked_term` normalises (NFKC, strip invisible/bidi/control chars) then screens via the `better-profanity` package (maintained wordlist + leetspeak variants — no slur list committed in-repo); word-based, so no substring false positives. `sanitize_text` cleans + caps length. Hit → reject + `audit_log`. |

## ACT plugin upload (`POST /api/backend/server/parses/ingest`)

The [EQ2LexiconACTPlugin](https://github.com/VortexUK/EQ2LexiconACTPlugin) sends each finished encounter here as an ACT-shaped JSON payload (`backend/server/api/parses.py:ingest_parse`). Bearer-token auth via `require_user_session_or_token`.

**`logger_server` field (plugin v0.1.10+ , server-side override added 2026-05-25)**:

Plugin auto-detects the EQ2 server from its log file path (`<install>/logs/<server>/eq2log_<character>.txt`) and stamps it as `logger_server` on every upload. The server uses it to override `EQ2_WORLD` for the Census guild lookup — so a Varsoon-configured deployment correctly resolves a Kaladim character's guild without needing per-deployment world config.

Backward compat: absent / null / empty `logger_server` → falls back to `EQ2_WORLD` env var as before. Older plugin versions and the local-ingest path keep working unchanged. The override path lives in `_resolve_uploader_guild_async(uploader, world=None)`.

**HMAC payload signing (v0.1.8+ plugin, server-side validator added 2026-05-25, strict mode same day)**:

Plugin computes `HMAC-SHA256(body_bytes, api_token)` and ships it as `X-Lexicon-Signature` (lowercase hex). Server reads the bearer token from the Authorization header, recomputes the HMAC over `await request.body()`, and `hmac.compare_digest`s against the header. Mismatch → 401.

Runs in **strict** mode (`_validate_payload_signature`): on token auth the header is required — absence is a 401 whose `detail` includes the releases URL so the user knows to update. Browsers (session-cookie auth) skip validation since they have no token-style key, but sending the header from a session client is a 400 (confused client > silent accept). The rollout went straight to strict because the user base is small and all pre-alpha; if you ever need an opportunistic-mode reintroduction, restore the `if not sig_header: return` early-out under a feature flag.

Threat model: the legitimate token holder can sign anything — this doesn't stop a user forging their own parse. What it does stop is (a) casual payload tampering by editing JSON in a debugging proxy, (b) MITM mutation of the body in flight, (c) replay using only a stolen token without the protocol knowledge to sign. Real integrity has to come from server-side sanity checks (DPS-vs-level caps, plausible encounter duration, cross-validation) layered on top.

## Web companion architecture

FastAPI backend + React/TypeScript frontend. Key design decisions:

- **Single env config**: `backend/server/config.py` exports `SERVICE_ID` and `WORLD`; web routes use `current_world()` from `backend/server/server_context.py` for the active per-request world (the bot still reads `WORLD` directly).
- **Stale-while-revalidate cache**: `backend/server/cache.py` TTLCache returns stale data immediately and fires a background refresh; hard-expires after 1 hr.
- **Circular import avoidance**: `_overview_to_char_response` in `guild.py` uses a local import of `_build_char_response` from `character.py` inside the function body.
- **Route split**: Large guild.py split into `guild.py` (roster + spellcheck + adorn), `guild_officer.py` (officer claim review), `item_watch.py` (item watch).
- **Frontend split**: `CharacterPage.tsx` exports `StatGroup`/`StatRow`; `CharacterAAsTab.tsx` and `CharacterSpellsTab.tsx` import them.
- **Spell icons**: served as static files at `/spell-icons/{id}.png`; backdrop + foreground layered with CSS `position: absolute; inset: 0`.

## Per-server architecture

A single deployment serves multiple EQ2 servers, each on its own subdomain (e.g. `varsoon.eq2lexicon.com`, `wuoshi.eq2lexicon.com`).

- **Middleware**: `backend/server/server_context.py` adds `ServerContextMiddleware` which reads the request `Host` header, resolves it to the matching row in the `servers` registry table, and stores it on a contextvar for the lifetime of the request. Non-prod environments also accept a `X-Server` header or `?server=` query-param override for testing.
- **Accessors**: all route code calls `current_world()` / `current_server()` (from `backend/server/server_context.py`) rather than the old fixed `WORLD` constant. The bot still uses `WORLD` directly (single-server).
- **Registry**: the `servers` table in `users.db` maps subdomain → world name + per-server settings (`max_level`, `current_xpac`, `launch_dt`, `display_name`). The registry is loaded into memory at startup via `load_registry()` and re-read after admin edits.
- **Per-server data**: character claims, item-watch rows, and parses each carry a `world` column so records are scoped to the server they belong to. Parses are additionally attributed by the `logger_server` field sent by the ACT plugin.
- **Universal data**: users, roles, and officer approvals are shared across servers (Discord identity is not server-specific).
- **Frontend bootstrap**: `GET /api/server` returns the active server's settings plus a `servers` array for the subdomain switcher; the frontend reads this once on load.
- **Single login**: `SESSION_COOKIE_DOMAIN` is set to the parent domain (e.g. `.eq2lexicon.com`) so one Discord login covers all subdomains. Leave it unset in local dev.
- **Seeding**: `EQ2_WORLD`, `SERVER_MAX_LEVEL`, `SERVER_CURRENT_XPAC`, and `LAUNCH_DT` env vars only seed the default server row on first migration; thereafter the registry is the source of truth and values are admin-editable per server.

## Frontend styling — Tailwind v4 (ENFORCED)

Tailwind v4 is the **single** styling system. There is no `tailwind.config.js` and no PostCSS — config is CSS-first in `frontend/src/index.css` via `@theme`. New frontend work MUST follow these rules; do not reintroduce the old patterns.

**The one rule:** Tailwind **utility classes for all static styling**; `style={{…}}` **only** for runtime-computed/dynamic values (data-driven colours, computed widths/positions, `gridTemplateColumns`, gradient-text, glows). Do **not** add new static inline `style` objects, and do **not** create per-page `CSSProperties` style-object consts.

- **Tokens → utilities**: design tokens live in the `@theme` block (`--color-*`, `--font-*`, `--radius-*`) and generate utilities — `bg-surface`, `text-gold`, `text-text-muted`, `border-border`, `text-rarity-fabled`, `font-heading`, `rounded-md`, etc. Spacing uses Tailwind's built-in 4px scale (`p-4` = 1rem). Use arbitrary values (`text-[0.88rem]`, `py-[0.45rem]`) only when no token/step fits.
- **Cascade layers**: `@layer base` (reset + element defaults) → `@layer components` (the `.btn`/`.card`/nav classes) → `utilities` (last, so page utilities win). Tailwind **Preflight is intentionally NOT imported** — the app has its own reset; keep it that way.
- **Rarity/tier colours**: ONE source of truth — `frontend/src/rarityColors.ts` (`itemRarityColor`, `recipeTierColor`, `qualityStyle`) backed by the `--color-rarity-*` tokens. Never define a new `TIER_COLOUR` map in a page.
- **Legacy `var(--*)` aliases**: `:root` still aliases the old names (`--gold` → `var(--color-gold)`, etc.) so the remaining *dynamic* `style={{}}` values resolve. Fine to reference in `style` for dynamic values; for static styling use the utility instead.
- **Exceptions (keep bespoke inline)**: `ItemTooltip`, `SpellScrollTooltip`, `AATree` faithfully recreate the in-game client (Times New Roman, computed glows/positions) — leave their inline styling alone.

## Shared frontend infrastructure (use these — don't hand-roll)

The 2026-05-29 cleanliness audit introduced a set of canonical primitives, hooks, and utilities. When writing new frontend code, reach for these BEFORE rolling your own — every hand-rolled version diverges and accrues drift. The original audit + plan live at `docs/superpowers/specs/2026-05-29-frontend-cleanliness-audit.md` and `docs/superpowers/plans/2026-05-29-frontend-cleanliness.md`.

### UI primitives — `frontend/src/components/ui/`

| Primitive | Use when |
|---|---|
| `<Button variant size>` | Any action button. `variant`: `primary`/`secondary`/`ghost`/`danger`. `size`: `sm`/`md`/`lg`/`icon` (icon = compact square for emoji/icon-only). |
| `<LinkButton>` | A `<button>`-styled `<a>` (external links that should look like buttons — e.g. the Support page Sponsor CTA). |
| `<Card>` | Any surface panel with the gold-tinted edge + soft shadow. Don't hand-roll `border border-border rounded bg-surface` divs. |
| `<SectionLabel variant>` | Uppercase eyebrow heading. `variant`: `gold` (default, brand) or `muted` (secondary headings in dense forms / admin tables). |
| `<Badge variant>` | Small rounded status label. `variant`: `success`/`warning`/`danger`/`info`/`muted`/`gold`. Replaces ad-hoc badge styling. |
| `<TabButton active onClick>` | The active-underline tab button (gold border-bottom on active). Wrap a group in `<div className="flex border-b border-border">`. |
| `<Textarea mono>` | Dark-theme textarea. `mono` for code/regex/markdown editors. Includes the Preflight reset so it doesn't render white. |
| `<DiscordButton href? children?>` | The "Sign in with Discord" link. Defaults to the right href + label; just `<DiscordButton />` is usually all you need. |
| `<SortTh sortKey active dir onSort>` | Pairs with `useSortable`. Click to toggle sort key/direction, renders the active caret. |

### Hooks — `frontend/src/hooks/`

| Hook | Use when |
|---|---|
| `useFetch<T>(url, opts?)` | Auto-fetch on mount + url-change. Returns `{ data, loading, error, statusCode, refetch }`. **Enforces `credentials: 'include'` by construction** — the P0 "missing credentials" class of bug can't happen if you go through this. Use `statusCode === 404` to detect "not found" / empty-state. |
| `useLazyFetch<T>()` | Tab-triggered or button-triggered fetches. Returns `{ data, loading, error, statusCode, run, reset }`; caller invokes `run(url)` on user action. |
| `useSortable<T, K>(rows, getValue, initialKey, initialDir?, defaultDirFor?)` | Manages sort key/direction over a tabular dataset. Pre-filter rows via `useMemo` before passing in. Pass `defaultDirFor` to make numeric columns default to descending on first click. |
| `useTooltipPosition({ x, y, width, ... })` | Viewport-aware fixed-position coords with right/bottom flip. Pure helper also exported: `clampTooltipPosition(opts)`. |
| `useItemTooltip()` | `{ tooltip, showTip, hideTip, moveTip }` — the boilerplate for hover-state-with-mouse-coords used by `<ItemTooltip>`. Colocated with `ItemTooltip.tsx`. |
| `useDebounce(fn, delay)` | Stable debounced wrapper around `fn`. Cleared on unmount automatically. (No `.cancel()` method yet — see follow-up task #197 if you need synchronous cancel.) |
| `useAuth()` + `isContributor(auth)` + `isUser(data)` | Auth state hook + the canonical "can the user edit?" derivation + a runtime type guard. Don't compute `auth.user.is_admin \|\| auth.user.static_roles.includes('contributor')` inline. |
| `useServer()` | Per-server bootstrap data (`world`, `displayName`, `maxLevel`, `currentXpac`, etc.) — see "Per-server architecture" above. |
| `useCensusStream<T>` (`subscribe<T>`) | SSE refresh stream. The `subscribe` API is generic; pass the type argument and you won't need an `as Character` cast. |

### Utilities — `frontend/src/lib/`

| Utility | Use when |
|---|---|
| `toErrorMessage(err: unknown)` | Replace `String((err as Error).message ?? err)` patterns. Sound narrowing — handles `Error`, `string`, and arbitrary thrown values. |
| `handle<T>(r: Response)` | Generic fetch response handler. Throws on non-ok, returns parsed JSON otherwise. Use in hand-rolled fetches (mutation endpoints in event handlers); for read paths, prefer `useFetch`. |

### Formatters — `frontend/src/formatters.ts`

`fmtNum`, `fmtNumOrDash`, `fmtDuration`, `fmtLocalDate`, `fmtLocalTime`, `fmtLocalDateTime`, `fmtRelative`. Don't reinvent date arithmetic with inline `new Date(unix * 1000)` — the formatters handle the `* 1000` and the locale/threshold logic. `fmtRelative` switches to a date string for anything older than ~8 weeks.

### Design tokens — `frontend/src/index.css`

| Token group | Notes |
|---|---|
| Surface + text | `--color-bg`, `--color-surface`, `--color-surface-raised`, `--color-border`, `--color-text`, `--color-text-muted` → `bg-*`, `text-*`, `border-*` utilities. |
| Brand | `--color-gold`, `--color-gold-bright`, `--color-gold-dim`, `--gold-rgb` (literal for `rgba(var(--gold-rgb), α)`). |
| Semantic | `--color-success`/`--success-rgb`, `--color-warning`/`--warning-rgb`, `--color-danger`/`--danger-rgb`. Use the token, NOT the hex. Past drift bugs (`#22c55e` vs `#4ade80`, etc.) traced to hex hardcodes. |
| Stat | `--color-stat-primary` (lime), `--color-stat-secondary` (cyan) — EQ2 stat colours. |
| Rarity | `--color-rarity-*` (common/handcrafted/treasured/legendary/fabled/mythical/ethereal/celestial/ancient). Via `rarityColors.ts`. |
| Discord | `--color-discord` — ONLY the Discord sign-in button. |
| Radius | `--radius-sm` (4px), `--radius-sm2` (6px — table cells/tooltips), `--radius-md` (8px), `--radius-lg` (12px), `--radius-pill` (999px) → `rounded-*` utilities. |
| Z-index ladder | `--z-header` (200), `--z-nav-backdrop` (250), `--z-nav-panel` (260), `--z-dropdown` (300), `--z-modal` (1000), `--z-tooltip` (9999) → `z-header`, `z-dropdown` etc. utilities. Use the token, not a `z-[N]` arbitrary value. |
| Fonts | `--font-heading` (Cinzel), `--font-body` (Spectral). `font-mono` is permitted for technical content (regex, hex, CLI). Tooltip recreations use Times New Roman via inline style. |

### File-split conventions

Pages that grow past ~700 lines are split into focused sibling files under a same-named subdir:
- `pages/admin/` — UsersTable, ClaimsTable, RoleRequestsTable, ServersSection, ParsesAdminTable, types.ts
- `pages/guild/` — GuildRosterTab, GuildSpellCheckTab, GuildAdornCheckTab, types.ts
- `pages/items/` — ItemSearchFilters
- `pages/parse/` — CombatantDetailPanel
- `pages/recipes/` — RecipeCard, ShoppingListPanel, QtyBtn, types.ts
- `components/act/` — TriggerEditor, SpellTimerEditor, ActImportPanel, primitives.tsx, types.ts

Shared types + className constants for a split page go in a sibling `types.ts`. Sub-components owning their own state + fetch logic are separate files; small inline render helpers (< 100 lines, tightly coupled) stay in the parent.

### When to break the rules

- **Game-client recreations** (`ItemTooltip`, `SpellScrollTooltip`, `AATree`) — these faithfully reproduce the in-game look (Times New Roman, computed glows, percentage-based coordinates). Inline `style={{}}` is *required*; don't try to "modernise" them.
- **`<Button>` doesn't fit** — sometimes a raw `<button>` with `appearance-none border-0 bg-transparent` is the right primitive (icon-only drag handles, hamburger triggers). Use raw + the Preflight-reset utilities.
- **`useFetch` doesn't fit** — for chained / dependent fetches that need to read each other's result mid-flight, keep a hand-rolled `useEffect`. Just remember `credentials: 'include'` + `res.ok` + cleanup.

### Mandatory testing rules

- **Module-load side effects need a no-throw vitest.** Any frontend module with top-level code that mutates globals (monkey-patches History/Location/etc., installs event listeners, runs heavy init work) needs at minimum `await expect(import('./mod')).resolves.toBeDefined()` in jsdom. The v5 historyTrace disaster shipped because this check was missing — `window.location.assign = fn` throws TypeError ("assign is read-only") at import time and broke the entire site. Reference pattern: the (now-deleted) `historyTrace.test.ts` from the 2026-05-29 diagnostic.

- **URL filter state needs a "setSearchParams can throw" test.** Browser extensions (ClearURLs, Privacy Badger, uBlock) and Firefox tracking-protection internals share the per-Document History API throttle quota. When depleted, `setSearchParams` throws `DOMException SecurityError`. Any component that uses `useSearchParams` for actively-clicked filter state should have a vitest that mocks setSearchParams to throw and asserts the UI still responds. RankingsPage uses the "React state as source of truth, URL as best-effort mirror via `safeSetParams`" pattern — copy from there; don't rebuild the URL-first pattern.

## Environment variables

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token from Discord developer portal |
| `CENSUS_SERVICE_ID` | Census API service ID (default `example`, rate-limited) |
| `EQ2_WORLD` | Default-server selector — selects the `servers` registry row treated as the fallback when no subdomain matches. Also used directly by the bot. Seeds the Varsoon row on first migration; runtime value comes from the registry. |
| `SESSION_COOKIE_DOMAIN` | Parent domain for the session cookie so one login spans both subdomains (e.g. `.eq2lexicon.com` in prod). Leave unset in dev. |
| `SERVER_CURRENT_XPAC` | Seed-only — runtime value is per-server in the `servers` table (admin-editable). Seeds the current expansion for the Varsoon row on first migration. |
| `SERVER_MAX_LEVEL` | Seed-only — runtime value is per-server in the `servers` table (admin-editable). Seeds the max character level for the Varsoon row on first migration. |
| `LAUNCH_DT` | Seed-only — runtime value is per-server in the `servers` table (admin-editable). Seeds the server launch datetime for the Varsoon row on first migration. |
| `ADMIN_DISCORD_IDS` | Comma-separated Discord IDs allowed to hit `/api/admin/*` and delete arbitrary parses |
| `PARSE_RETENTION_DAYS` | Days after the fight before the retention sweep (`backend/server/parses/cleanup.py`) hard-deletes a trash parse and collapses a named fight's duplicate uploads to its primary. Default `3`. |
| `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` | Twitch app (client-credentials) creds for the raid-schedule "Raiding live" list (`backend/server/raid_live.py`). Both optional — unset ⇒ live list disabled, raid schedule unaffected. |
| `DB_USERS_PATH` | Override the default `data/users.db` location (set on Railway to the persistent-volume mount) |
| `DB_PARSES_PATH` | Override the default `data/backend/server/parses/backend.server.parses.db` location (set on Railway to the persistent-volume mount) |
| `DB_CENSUS_PATH` | Override the default `data/backend/census/census.db` location (persistent last-known character/guild lookups for resilient caching). Set on Railway to the persistent-volume mount; the `.db` is gitignored + generated at runtime. |
| `DB_ZONES_PATH` | Override the default `data/zones/zones.db` location. Set on Railway to the persistent-volume mount (the `.db` itself is not committed — uploaded manually; see "Manual upload: zones.db" below). |
| `DB_RAIDS_PATH` / `DB_ITEMS_PATH` / `DB_SPELLS_PATH` / `DB_RECIPES_PATH` / `DB_CLASSES_PATH` | Same pattern: env-var override of the default `data/<name>/<name>.db` location. See `.env.example` for the grouped block. |
| `R2_ENDPOINT` | Litestream backups → `https://<account>.r2.cloudflarestorage.com` |
| `R2_BUCKET` | Litestream backups → bucket name (e.g. `eq2lexicon-backups`) |
| `R2_ACCESS_KEY_ID` | Litestream backups → R2 API token Access Key ID |
| `R2_SECRET_ACCESS_KEY` | Litestream backups → R2 API token Secret Access Key |

## Census API patterns

Base URL: `https://census.daybreakgames.com/s:{service_id}/json/get/eq2/`
- Item: `item/?displayname=<name>` / `item/?id=<id>` — game-link IDs are signed 32-bit; convert negatives with `+= 2**32`
- Guild: `guild/?name=<name>&world=<world>&c:resolve=members(...)&c:show=member_list,name,world,rank_list`
- Character spells: `character/?name.first=<name>&locationdata.world=<world>&c:resolve=spells(name,tier_name,type,level,given_by)&c:show=name,spell_list`
- Character AAs: `character/?name.first=<name>&locationdata.world=<world>&c:show=name,alternateadvancements` — response has `alternateadvancements.alternateadvancement_list[]{tier, treeID, id}` where `id` == `nodeid` in tree JSON

## AA tree notes

Data in `data/AAs/trees/{id}.json` and `data/AAs/icons/{id}.png`. Each node has `nodeid`, `xcoord`, `ycoord`, `icon.id`, `icon.backdrop`, `maxtier`, `classification`. `bg_sprite.png` has backdrop circles (44px) and badge circles (24px) — see `backend/image/aa_tree.py` for exact offsets.

`detect_tree_type()` returns: `class`, `subclass`, `shadows`, `heroic`, `tradeskill`, `tradeskill_general`, `warder`, `prestige`, `dragon`, `reign_of_shadows`, `far_seas`, or `unknown`. The last six fall back to `render_subclass_tree` pending calibration. Coordinate systems are native 640×480 (SCALE=2 → 1280×960); full arithmetic is in `backend/image/aa_tree.py`. The `/aacheck` command offers five static tree choices (Class/Subclass/Shadows/Heroic/Trade) to avoid autocomplete API calls.

## Tooltip rendering notes

Quality tier colours, primary/secondary stat colours, stat ordering, and class-list collapsing are all in `backend/image/tooltip.py`. The config-driven `ITEM_DISPLAY` / `TYPEINFO_DISPLAY` dicts in `backend/census/constants.py` control which extra info rows appear (Type, Slot, Mitigation, Level, Charges, Duration, etc.).

## Guild and spellcheck command notes

**Guild**: members without a `type` dict are filtered out; rank resolved via `rank_list`; columns are Rank / Name / Class (Level) / AA / Tradeskill (Level) / Deity; sorted by rank ID asc then level desc; sent as `.txt` attachment if > 2000 chars.

**Spellcheck**: strips trailing Roman numerals, keeps highest-level entry per base name per type. Bot filter: `level > 0`, type in `(spells, arts)`, `given_by NOT IN (alternateadvancement, class)`. Web filter: same but `given_by == 'spellscroll'` (covers scribed mage spells and combat arts). Blocklist applied in both paths.

## Spell blocklist

`data/spells/blocklist.json` holds base spell names (no Roman numerals) to suppress:
```json
{ "blocked": ["Fighting Chance"] }
```
- `load_blocklist()` in `spells_db.py` re-reads the file on every call
- Applied in both the web `/spells` endpoint and the Discord `/spellcheck` cog
- Add to `blocked` and the change takes effect without a restart

## Local testing scripts

See [scripts/README.md](scripts/README.md) for the full list of preview, download, and DB-build scripts.

### Raid-strategies seed pipeline

Two-stage by design — the network-dependent scrape is decoupled from the fast local DB ingest:

  1. **Scrape** (`scrape_eq2i_raids.py`) — fetches EQ2i zone/encounter pages via the polite-API cache (`scripts/dev/.eq2i_cache/`, gitignored). Produces JSON only. Discovers zones via `zones_db.list_by_expansion` filtered to raid types — no hardcoded URL list.
  2. **Ingest** (`ingest_raids_json.py`) — reads the JSON, calls `raids_db.upsert_raid_zone` + `upsert_raid_encounter` with `source=SOURCE_SCRAPE`. The helper **skips rows with `source=SOURCE_MANUAL`** on re-scrape, so a re-run never clobbers a human edit.

`eq2_raid_data.json` (the full-scrape output) is **committed** so a fresh clone can run `ingest_raids_json.py` without re-scraping. The intermediate HTTP cache and 3-zone sample JSON are gitignored.

## Deployment

- Platform: Railway, Nixpacks builder
- Push to `main` branch triggers redeploy
- New slash commands may take up to 1 hour to propagate globally, but appear instantly in the registered guild IDs above
- Do not push until the user confirms local testing passes

### Backups (backend.server.parses.db + users.db → Cloudflare R2)

[litestream](https://litestream.io) replicates both SQLite DBs to R2 in near-real-time. On container start, `litestream restore -if-replica-exists` rehydrates either DB from R2 if the Railway volume is wiped. Config in `litestream.yml`; orchestration in `railway.toml`'s `startCommand`.

**One-time R2 setup:**

1. Sign in at https://dash.cloudflare.com → **R2** → Create bucket (e.g. `eq2lexicon-backups`).
2. Note the **Account ID**; the S3 endpoint is `https://<account_id>.r2.cloudflarestorage.com`.
3. **R2 → API tab → Create API Token** (NOT "Account API tokens" — that mints Cloudflare REST tokens, not R2 S3 tokens). Permissions: Object Read & Write; specify the bucket.
4. Copy **Access Key ID** and **Secret Access Key** from the success screen.
5. In **Railway → Variables**, add `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.
6. Verify in R2 console — `backend/server/parses/` and `users/` prefixes should appear within a minute of next deploy.

If logs show `cannot fetch generations: Unauthorized` 401s, the wrong token type was minted — re-mint via the R2 API tab specifically.

**Manual restore:**

```bash
litestream restore -config /app/litestream.yml /app/data/backend/server/parses/backend.server.parses.db
litestream restore -config /app/litestream.yml -timestamp 2026-05-24T18:00:00Z -o ./backend.server.parses.db-snapshot /app/data/backend/server/parses/backend.server.parses.db
```

If R2 env vars are absent, the startCommand's `|| true` makes the restore a no-op and the app still runs.

### Manual upload: zones.db

`data/zones/zones.db` is zone-metadata reference data built from the cleaned wiki dump. It's gitignored, lives on the Railway persistent volume, and is replicated by litestream. Boss rosters are **web-editable** — `build_zones_db.py` only writes zones/types/aliases; boss data is curator-managed in-place.

Refresh procedure (zone metadata only):

```powershell
python scripts/dev/clean_eq2_zones.py     # source → cleaned JSON
python scripts/build_zones_db.py          # cleaned JSON → SQLite
python scripts/dev/_smoke_test_zones.py
python scripts/dev/_smoke_test_zones_db.py
```

Then upload to the Railway volume and set `DB_ZONES_PATH` if your mount differs from `/app/data/zones/zones.db`. Schema changes require a full rebuild + re-upload — rebuilds do **not** touch `zone_encounters` / `zone_encounter_mobs`, so curator boss data is preserved.

### Manual upload: raids.db (first-time seed)

`data/raids/raids.db` is hybrid — wiki-seeded strategies plus user edits + revision history. Don't clobber it on every deploy.

1. **First-time seed** (once, before any production user edits):
   ```powershell
   python scripts/dev/scrape_eq2i_raids.py --all-raids
   python scripts/dev/ingest_raids_json.py --in scripts/dev/eq2_raid_data.json
   ```
   Upload `data/raids/raids.db` to the Railway volume. Set `DB_RAIDS_PATH` if needed.

2. **Refreshing scraped content**: re-run the scrape, then run ingest in place. `upsert_raid_encounter` skips `SOURCE_MANUAL` rows — human edits survive every re-scrape.

## Logging conventions

- **Module-level binding**: `_log = logging.getLogger(__name__)`.
- **Lazy `%s` formatting**: `_log.info("foo %s", x)` — never f-strings inside log calls.
- **Bracketed prefix per module**: `[lowercase-with-hyphens]` (e.g. `[cache]`, `[census-refresh]`). Helps grep by component.
- **For audit events**: use `audit_log("snake_case_action", actor=..., **fields)` from `backend.server.core.audit_log`. Don't hand-roll `_log.info("[audit] …")`.
- **Levels**: WARNING for security signals (HMAC mismatch, invalid token); INFO for audit-trail / startup config / state changes; DEBUG for per-request noise; ERROR/exception for "needs investigation". Recoverable Census flakes are WARNING, not ERROR (per the 2026-05-30 audit).
- **Env vars**: `LOG_LEVEL` (default `INFO`) and `LOG_FORMAT` (`text` default, `json` for Railway) are read by `configure_logging()` in `backend/core/logging_config.py` at startup.
- **Sensitive values that NEVER appear inside a log-call argument list**: bearer tokens, HMAC signature bytes, `DISCORD_CLIENT_SECRET`, OAuth `access_token`s, the `raw` token from `mint_api_token`.

## Frontend design principles

When building or changing the React frontend, hold to these — the goal is a distinctive, cohesive interface that reads as *deliberately designed for an EverQuest 2 guild tool*, not a generic dashboard. (Implementation rules live in [Frontend styling — Tailwind v4](#frontend-styling--tailwind-v4-enforced) above; these are the aesthetic intent behind them.)

- **Typography**: Use characterful, intentional fonts. Headings are **Cinzel** (`font-heading`) — a classical serif fitting Norrath's high-fantasy tone; body is **Spectral** (`font-body`), a screen serif that reinforces the "lexicon/tome" voice. In-game-style tooltips deliberately use Times New Roman to mirror EQ2's client. Never introduce generic UI fonts (Inter, Roboto, Arial, system fonts) for display text.
- **Color & theme**: One cohesive palette — gold (`--color-gold`) on deep stone/parchment. Gold is the single accent (links, focus, active states); Discord blurple is confined to the sign-in button only. Dominant base colours with sharp metallic accents beat timid, evenly-distributed palettes. Avoid the clichéd purple-gradient-on-white "AI slop" look.
- **Motion**: Favour CSS-only transitions. Spend the budget on a few high-impact moments (the staggered page-load reveal via `.page-enter`) rather than scattering small effects; honour `prefers-reduced-motion`.
- **Backgrounds & depth**: Atmosphere via the layered background overlay (warm top-glow + vignette) and the gilded card treatment (gold-tinted edge, soft shadow, top hairline) — not flat fills. Keep it legible.
- **Cohesion over novelty**: Every page should feel part of the same product. Reuse the theme utilities and the `ui/` primitives; never reinvent spacing, card, or button styles per page.
