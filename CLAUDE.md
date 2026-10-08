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
| `backend/eq2db/spells.py` | Spell catalogue (Postgres `spells` schema) — `SpellCatalogue` class (shared `catalogue` instance): strip_roman, unique_highest_entries, load_blocklist, find_by_ids, find_by_crc, spell_to_row, upsert_spells. Every eq2db module follows this convention: pure helpers are staticmethods with full bodies in the class, DB reads are instance methods on a per-instance identity (`schema` for the Postgres families, `path` for the committed SQLite reference data aas/classes), and tests lease scratch schemas (fixtures in `tests/fixtures/catalogues_db.py` + `tests/fixtures/pg.py`). ALL data families live in **Supabase Postgres** — there is NO SQLite anywhere in the backend (Phase 3, 2026-10) — one database, one schema per family. DDL is owned by `db/migrations/NNNN_<family>.sql`, applied by `backend/pg_migrate.py` from the app lifespan (deploy = migrate; ledger `public.schema_migrations` + advisory lock). Connections come from `backend/pg.py` (lifespan-owned psycopg pools, pool-if-open-else-direct; schema passed to `connection(schema)` / `aconnection(schema)` / `getconn(schema)` so all SQL stays unqualified — the `SET search_path` is session-level and only sent when the physical connection's remembered schema differs; `autocommit=True` (or `PgStoreBase._read()`) makes a single-statement read one round trip instead of BEGIN+query+COMMIT; raw SETs on a pooled connection must be followed by `pg.forget_schema(conn)`, multi-schema transactions use `local_search_path_sql`; `%s` params, dict rows). The users domain stores are `XStore(PgStoreBase)`; `CensusStore` / `ParsesStore` / `ZoneCatalogue` / `RaidCatalogue` / `ItemCatalogue` / `SpellCatalogue` / `RecipeCatalogue` are `PgCatalogue` (catalogues add `READY_TABLE`/`ready()` — the route-level 503 gate that replaced the file-exists checks) — `init_db()` KEEPS ITS NAME but returns a pooled schema-scoped connection proxy (close() returns to pool). The `backend.server.db` facade re-exports are unchanged; tests lease scratch schemas via `tests/fixtures/pg.py` (TEST_DATABASE_URL, local PG17) and re-point `store.schema`. |
| `backend/eq2db/recipes.py` | Recipe catalogue (~66k rows, Postgres `recipes` schema) — `RecipeCatalogue`: find_by_id, find_by_name, find_by_spell, find_spells_by_tier, find_by_output_id. Secondary components stored as JSON array (text). `out_level` is loader-owned (scripts/backfill_recipe_levels.py cross-schema UPDATE), never written by the upsert. |
| `backend/eq2db/aas.py` | AA catalogue (Postgres `aas` schema; migration 0012 seeds the full dataset). Tables `aa_trees` (tree_type + max_points precomputed at build) + `aa_nodes` (incl. per-node unlock rules: points_to_unlock, classification_points_required, first_parent_id/tier) + `aa_limits` (per-xpac caps + `visible_rows` era node curation from aa_limits.json — pre-SF xpacs hide class rows 5-6 / subclass rows 16+19, verified against live Wuoshi census). Accessors: `load_tree_index`, `tree_node_costs`, `tree_max_points`, `total_max_points`, `get_tree`, `xpac_limits` (short-code alias tolerant; returns aa_cap + unlocked_trees + visible_rows); `detect_tree_type` is build-time only. Refresh: `scripts/download_aa_trees.py` (tree JSONs are local intermediates, gitignored) then `scripts/build_aas_db.py` (upserts into the schema at DATABASE_URL). Era filtering surfaces via `/api/aa/config`.`visible_rows` → `filterTreeForEra` in CharacterAAsTab. |
| `backend/eq2db/zones.py` | Zone catalogue (~1124 rows, Postgres `zones` schema) — `ZoneCatalogue` over frozen dataclass models (`Zone`, `ZoneEncounter`, `ZoneEncounterMob`, `FeaturedRaid*`). Tables: `zones`, `zone_types`, `zone_aliases`, `zone_encounters`, `zone_encounter_mobs` + the featured-raid trio. Lookups: `find_by_name`, `list_by_expansion`, `list_by_event`, `list_by_type`, `list_bosses_for_zone`, `find_zones_by_boss`. Zone metadata from `scripts/dev/eq2_zones.cleaned.json`; rebuild via `scripts/build_zones_db.py`. Boss data is curator-managed in-place. |
| `backend/census/wikitext_md.py` | MediaWiki wikitext → markdown converter (mwparserfromhell). Handles EQ2i templates, wikilinks, nested lists, headings, bold/italic. Used by the raid scraper. |
| `backend/eq2db/raids.py` | Raid-strategy catalogue (Postgres `raids` schema) — `RaidCatalogue`: `raid_zones` + `raid_encounters` (markdown blob per encounter) + `raid_encounter_revisions` + ACT trigger/spell-timer tables (helpers folded in from the former raids_act.py). `SOURCE_*` provenance tokens mirrored as class attributes. |
| `backend/census/store.py` | Persistent store (Postgres `census` schema; characters + guilds keyed by name_lower + world, `data_json` jsonb) — `CensusStore(PgCatalogue)`, shared `store` instance (consumers alias it `census_store`). Keep-best-known merge — sparse Census refresh never nulls good data. Also `guild_history` (one row per guild per UTC day: level/members/accounts/achievements + max-level and distinct-class roster counts) written by `guild_cache._persist_and_publish_guild` via `upsert_guild_history` (prunes past `GUILD_HISTORY_RETENTION_DAYS`=400); served store-only by `GET /api/guild/{name}/history?days=` (never Census; empty list for an unseen guild) → the guild page History tab (`pages/guild/GuildHistoryTab.tsx`, lazy, Recharts in the `vendor-charts` chunk; `components/charts/GuildHistoryChart.tsx` — one axis per chart, fixed colour per series). Chunk naming caveat in `frontend/vite.config.ts`: rolldown puts React's CommonJS body in the alphabetically-first manual chunk, hence `core-react` (check `modulepreload` in dist/index.html after any manualChunks change). |
| `backend/image/tooltip.py` | PIL renderer for item tooltips. Renders at 2× then downsamples (SCALE=2, ZOOM=1.3). See file for colour/stat/ordering details. |
| `backend/image/aa_tree.py` | AA tree renderers and coordinate systems. See file for tree-type detection and coordinate arithmetic. |
| `backend/bot/bot.py` | Registers all cogs, syncs slash commands to three specific guild IDs (648253204760625160, 955890381847928892, 1502314690041221260) for instant propagation plus a global sync. Also: shared app-command error handler, tracked background-task set cancelled in close() (the bot races the web lifespan, but pre-lifespan queries fall back to direct psycopg connections and guild_context degrades on psycopg.Error — no defensive init needed), and `intents.members = True` — the SERVER MEMBERS privileged intent MUST be toggled in the dev portal or login fails (main.py catches it, logs CRITICAL, web half keeps running). |
| `backend/bot/guild_context.py` | Per-Discord-guild context: `resolve_guild_context(discord_guild_id)` reads the `discord_guild_links` registry (users schema, edited via `/lexicon` link/voice/status/unlink, manage_guild gated) → GuildContext(world, guild_name, voice_channel_id, linked). Unlinked/DM → `FALLBACK_WORLD = "Wuoshi"` (hardcoded by design). All world-aware cogs resolve here — the env WORLD pin is bot-dead. |
| `backend/bot/render.py` + `messaging.py` | Pure text half of bot responses (table builders, `plan_code_block` → SendPlan for the 2000-char wrap-vs-file decision; fully unit-tested in tests/bot/) + the one discord I/O sender `send_plan`. Cogs are thin adapters. |
| `backend/bot/cogs/voice_attendance.py` | Phase 3 voice cross-check: tasks.loop every 120s → for each `/lexicon voice`-configured guild, if `attendance_store.find_live_session(world, guild, now)` (merge-gap window) → snapshot the voice channel members → `record_voice` kind='voice' observations (character_name carries the discord id). Site rollup shows 🎧 + "in voice, not in game" on AWOL. Idle path = 1 SQL per linked guild, no Discord API calls. |
| `backend/bot/cogs/items.py` | `/item` — accepts name, numeric ID, or game link |
| `backend/bot/cogs/guild.py` | `/guild` — tabular member list sorted by rank then level |
| `backend/bot/cogs/spellcheck.py` | `/spellcheck` — spell tier summary or full list (`details:True`) |
| `backend/bot/cogs/aacheck.py` | `/aacheck` — renders a character's AA tree with tier badges |
| `backend/server/config.py` | Single source of truth: SERVICE_ID, WORLD from env vars |
| `backend/server/server_context.py` | Host → active-server middleware + contextvar accessors (`current_world()`, `current_server()`) + in-memory registry loaded from the `servers` table |
| `backend/server/api/server.py` | `GET /api/server` — bootstraps the frontend with the active server's world, display name, max_level, current_xpac, launch_dt, and the full public server list |
| `backend/server/cache.py` | TTLCache with stale-while-revalidate: character_cache, guild_cache, claim_cache. Character and guild read paths serve from `census_store` first and never block on Census. |
| `backend/server/api/aa.py` | GET /api/character/{name}/aas — AA profile list with per-tree data |
| `backend/server/api/aa_plans.py` | AA planner saved builds: CRUD (owner-scoped in SQL) + read-only share by always-minted slug (`GET /api/aa/plan/{slug}`, page `/aa-plan/{slug}`). Store `backend/server/db/aa_plans.py` (users schema). Rule legality is the frontend engine's job (`frontend/src/pages/aaplanner/engine.ts` — point-cost-weighted self-exclusive thresholds, parent ranks, flat 100/tree cap, separate tradeskill pool, no-stranding removals); the server validates structurally. Planner UI = "Planner" mode on the character AA tab (`aaplanner/PlannerMode.tsx`, interactive AATree: click spend / right-click refund). |
| `backend/server/api/character/gear_sets.py` | GET /api/character/{name}/gear-sets — saved in-game equipment sets (Census `adventure_sets`), store-first SWR mirroring aa.py; feeds the character-sheet set pills + the compare per-side set picker. Each set carries `stat_deltas` (set − worn, additive stats + active item-set bonuses, from items.db `item_stats`) computed in sibling `stat_deltas.py` — the sheet shows approximated stats when a set is selected |
| `backend/server/api/character/rankings.py` | GET /api/character/{name}/rankings — WCL-style per-boss summary over the /api/rankings kills dataset (shared 60s cache). Gated to curated zones.db content only (same universe as the rankings dropdowns — heuristic-matched kills never surface); zone sections carry `expansion` and the response lists the character's `expansions` (newest first) for the tab's xpac dropdown. Class-scoped rank percentiles (best/median vs every same-class parse), All Stars points = 100×best/class-record + rank among class peers (per boss + zone total; peers get credit for bosses the target never killed). Both dps+hps in one response. `zones: []` ⇒ character page hides the Rankings tab (`CharacterRankingsTab.tsx`; percentileColors.ts scale). Fake local data: `scripts/dev/seed_fake_parses.py --character X` (tagged uploaded_by='fake-seed', `--wipe` to remove). |
| `backend/server/api/characters.py` | GET /api/characters/search — character name search |
| `backend/server/api/guild_officer.py` | Officer claim-review endpoints; imports _officer_chars, _roster_rank_map from guild.py |
| `backend/server/api/item_watch.py` | Item watch endpoints; imports _officer_chars, _roster_rank_map from guild.py |
| `backend/server/api/stats.py` | Per-server Stats page from the census `character.stat` aggregate family (global/world/world×class rows, 12 lifetime stats as max/sum/avg, recomputed daily by census). `GET /api/stats/server` = population, class distribution, totals, named leaderboards (single-value stats census-sort; K/D sorts `.value` with a 1000-kill floor; the two-key hit records can't census-sort — range-filter near the aggregate max + client-side sort). `GET /api/stats/character/{name}` = lifetime panel w/ ability names via spells.db `find_by_crc` (crc 4294967295 = sentinel, skip). classid = classes.db `icon_id` (Templar=13). 6h SWR module cache per world; leader queries retry (census drops back-to-back sorted queries). Never builds inline: cold cache → 202 `{"status":"building"}` + background build (lock-deduped, frontend polls 5s); startup prewarm via `prewarm_server_stats()` in app.py lifespan; failed build → 60s cooldown → 503. `GET /api/stats/explore?stat=&cls=` = live top-20 by whitelisted stat (`_EXPLORE_STATS`: combat snapshots under `stats.combat.*`/health/power + progression scalars quests.complete / collections.complete / achievements.points/.completed / alternateadvancements.spentpoints, projected narrowly — never `c:show=achievements`, it drags the 500+-item list + lifetime `statistics.*`), optional class scope — census needs numeric `type.classid` (string `type.class` filters silently return empty); 15-min cache, stale-beats-error. |
| `backend/server/api/export.py` | Read-only third-party export API (issue #219, "Warboard"): versioned `/api/export/v1/{filters,rankings,parses/{ids}/abilities}`. Auth = bearer token + admin-granted `api` role (KNOWN_ROLES; admins pass) — opt-in, attributable, revocable two ways. Reuses the rankings kills dataset + `_encounter_detail_sync`; player rows use the rankings' `_is_player_combatant` predicate so ranked characters always appear; never exposes `source_dsn`/uploader discord ids. Pydantic models are the stable contract — breaking changes go to /v2/, never mutate v1. Token-keyed rate limits (300/hour). |
| `backend/server/census_health.py` | Site-wide Census availability signal: background poll every 5 min; `is_down()`/`get_state()` read by the read/refresh paths. Flips to down only after `PROBE_FAILURES_TO_TRIP` (2) consecutive failed probes (the live breaker in `census/failures.py` covers real-request bursts); transitions log at WARNING (down) / INFO (up). |
| `backend/server/census_events.py` | In-process async pub/sub backing the SSE stream (single-process only). |
| `backend/server/census_refresh.py` | Background refresh orchestration (throttle 15 min / in-flight dedupe / skip-when-down); merges into census_store, updates cache, publishes SSE. `_merge_roster` best-known join. |
| `backend/server/api/census.py` | `GET /api/backend/census/health` (first-paint snapshot) + `GET /api/backend/census/stream` (SSE: character/guild refresh records + health changes). The stream is session-gated (401 anonymous — the login page's EventSource simply closes), capped at `MAX_STREAM_SUBSCRIBERS` (503), and filtered to the request's world: every refresh event carries `world`, and one process serves every subdomain. |
| `backend/server/parses/fights.py` | Fights = the mirror-group an upload belongs to, decided ONCE at ingest (`attach_encounter`, called from `_insert_encounter_rows_sync`) against the few existing `fights` rows sharing world + `boss_key(title)` + guild inside `PARSE_MIRROR_WINDOW_S`, with the list grouper's rules (different uploader, window, top-N roster containment vs the fight's longest upload). Table `parses.fights` (migration 0022) carries `primary_encounter_id` (longest visible upload = list canonical) and `primary_winning_encounter_id` (longest visible+verified+winning = the rankings kill), recomputed by `refresh_fight` from the store hooks (`delete_encounter`, `soft_delete_encounter`, `unhide_encounter`; `set_encounter_guild_name` re-attaches because guild is part of the key). `backfill_world` groups any `fight_id IS NULL` rows (bulk grouper above `BULK_THRESHOLD`, under a per-world advisory lock) — the rankings loader calls it first, so pre-migration rows and seeded test rows need no special casing. Rankings read `fights JOIN encounters ON primary_winning_encounter_id` (no regrouping) and `rankings.sync_encounter` patches one fight's kill into the cached dataset after each upload (BackgroundTasks), so boards update within seconds while the 10-min SWR rebuild stays as the reconciler. The retention sweep collapses aged fights to their two primaries. |
| `backend/server/parses/cleanup.py` | Parses retention sweeps (`run_parse_cleanup`). Rows: trash (non-boss) hard-deleted `PARSE_RETENTION_DAYS` after the fight; named (boss) fights keep only the primary (longest) upload after the same window. Reuses `_group_into_fights` (rankings-safe); never touches soft-deleted rows. **Detail**: kept fights' `attack_types`+`damage_types` are dropped on a zone-tier schedule (`_classify_zone`: curated raid 30d / curated group-instance 14d / other 7d, env-overridable `PARSE_DETAIL_RETENTION_*_DAYS`); `encounters.detail_pruned_at` is stamped and the parse page shows a "breakdown pruned" notice. encounters+combatants (rankings/list/character pages) live forever. The cutover copy script applies the same tiers at initial load. Run periodically by `app.py:_parse_cleanup_loop`. |
| `backend/server/api/guild_settings.py` | Per-guild switches, LEADER-or-admin write (Census rank_id 0 — `_leader_chars` / `_leader_chars_cached` in guild.py, the first place rank 0 is distinguished from `_OFFICER_RANKS`), public `GET /api/guild/{name}/settings`, `PUT` audited as `guild_settings_updated`. Store `backend/server/db/guild_settings.py` (`guild_settings` table in the users schema, one boolean column per switch, absent row == defaults). Only switch today: `officers_can_delete_parses` (default on) — enforced in `parses/delete.py::_can_delete_encounter` (memoised per guild per request) and `parses/list.py::_compute_permissions` (one IN-query, officers only); gates ONLY officer parse deletion — uploaders keep their own uploads, admins unaffected. `officer-status` also returns `is_leader`; the guild page shows a Settings tab to leader/admin. Added after 2026-09-27. |
| `backend/server/db/erasure.py` | Right-to-erasure (privacy policy §8, 2026-09-28): `erase_user_sync(discord_id)` — ONE Postgres transaction spanning the users and parses schemas (search_path switched mid-transaction; users FKs deferred), so a crash can never leave a half-erased account — rows that ARE the person are deleted (users, tokens, roles, requests, claims, favourites, downloads, AA plans, availability, voice observations, tamper reports), `*_by` author columns are tombstoned to the placeholder user `deleted`, uploads stay as guild records with `source_dsn` → `plugin:deleted` and `hidden_by` cleared. Routes: admin `DELETE /api/admin/users/{id}` (not self) and self-service `DELETE /api/auth/me` (body `{confirm: <discord username>}`, clears the session); both audit `user_erased` and clear the claim cache, supporters cache and metrics last-seen map. Privacy page `frontend/src/pages/PrivacyPage.tsx` at `/privacy` is the one route rendered without a login (`PUBLIC_PATHS` + `PublicShell` in App.tsx) — keep its text in step with the code. Companion retention: `VOICE_OBSERVATION_RETENTION_DAYS` (90) swept in `app.py:_parse_cleanup_loop`; `/api/supporters` is session-gated and returns display names; fonts are self-hosted from `frontend/public/fonts` (`src/fonts.css`, regenerate with `scripts/dev/selfhost_fonts.py`) so no visitor IP reaches Google. |
| `backend/server/db/site_settings.py` | Site-wide (not per-server) key/value settings in the users schema (`site_settings`), facade aliases `get_site_setting` / `set_site_setting` / `all_site_settings`; absent row == unset. Only key today: `discord_invite_url` — admin `GET`/`PUT /api/admin/site-settings` (regex-validated `https://discord.gg/<code>` or `https://discord.com/invite/<code>`, audited `site_settings_updated`), delivered to the frontend in the `GET /api/server` bootstrap as `discord_invite_url` → `useServer().discordInviteUrl` → `components/DiscordCommunityLink.tsx` (icon in the header's top-right cluster + button on the Support page; renders nothing when unset). Admin UI: `pages/admin/SiteSettingsSection.tsx`. The invite must be made permanent in Discord (Expire after: Never, Max uses: No limit); the site never validates it live. |
| `backend/server/api/guild_recruitment.py` | Guild recruitment API — public read, officer-or-admin write (raid_schedule's `_officer_chars` gate). Profiles keyed by CENSUS GUILD ID in the store (`backend/server/db/guild_recruitment.py`, users schema) so a profile + logo survive guild renames; routes stay name-addressed and every officer save re-resolves the id + refreshes the stored name. Fields: recruiting flag, blocklist-screened description (≤1000), needed classes (validated vs classes.db adventure names), curated tags (`RECRUITMENT_TAGS`), ≤3 roster-validated in-game contacts, `DISCORD_INVITE_RE` link. Logo: base64 JSON upload → PIL re-encode (`_process_logo`: format+dims header-checked pre-decode, exif_transpose, ≤200px WebP q85, ≤128KB) stored as bytea in the users schema (inside the Postgres backups); served with ETag/304 + nosniff; upload/removal audited with the actor (`guild_logo_uploaded`/`guild_logo_removed` — the abuse trail). `GET /api/recruiting` lists recruiting guilds + member counts via `census_store.latest_guild_member_counts` (guild_history). Daily sweep `backend/server/recruitment_sweep.py` (app lifespan): census lookup BY ID per listed guild — renamed → stored name refreshed, gone → auto-delisted (`recruiting=0`, never deleted), census error → untouched. Erasure tombstones `updated_by`/`logo_uploaded_by`; the logo blob stays (guild asset). Frontend: guild-page Recruitment tab (`pages/guild/GuildRecruitmentTab.tsx`, view for everyone + officer edit) and browse page `pages/RecruitingPage.tsx` at `/recruiting` (nav in BOTH App.tsx BROWSE_ITEMS and MobileNav GROUPS). |
| `backend/server/api/raid_schedule.py` | Guild raid-schedule API. Public `GET /api/guild/{name}/raid-schedule`; officer-or-admin `PUT` (full replace, ≤4 teams × ≤4 raids, each ≤5h, IANA tz, Twitch validated, free text blocklist-screened). Clearing == PUT with empty `teams`. `GET /api/raiding-live` returns the current world's live teams. Tables `raid_teams`/`raid_slots` in the users schema (`backend/server/db/raid_schedule.py`; `raid_slots.days` is `integer[]`). |
| `backend/server/raid_live.py` | Twitch-verified "Raiding live" poller (`poll_loop`, `app.py` lifespan). Finds teams inside a scheduled window (team tz, ±15min grace) then verifies live via Twitch Helix; caches per world for `/api/raiding-live`. No-ops without `TWITCH_CLIENT_ID/SECRET`. |
| `backend/server/core/twitch.py` | `parse_twitch_login` (accept only `twitch.tv/<channel>`) + `is_blocked` (thin wrapper over `text_moderation`). |
| `backend/server/core/text_moderation.py` | Shared profanity screen + input sanitiser for officer free text (raid team names/labels) and Twitch logins. `contains_blocked_term` normalises (NFKC, strip invisible/bidi/control chars) then screens via the `better-profanity` package (maintained wordlist + leetspeak variants — no slur list committed in-repo); word-based, so no substring false positives. `sanitize_text` cleans + caps length. Hit → reject + `audit_log`. |

## ACT plugin upload (`POST /api/backend/server/parses/ingest`)

The [EQ2LexiconACTPlugin](https://github.com/VortexUK/EQ2LexiconACTPlugin) sends each finished encounter here as an ACT-shaped JSON payload (`backend/server/api/parses.py:ingest_parse`). Bearer-token auth via `require_user_session_or_token`.

**`logger_server` field (plugin v0.1.10+ , server-side override added 2026-05-25)**:

Plugin auto-detects the EQ2 server from its log file path (`<install>/logs/<server>/eq2log_<character>.txt`) and stamps it as `logger_server` on every upload. The server uses it to override `EQ2_WORLD` for the Census guild lookup — so a Varsoon-configured deployment correctly resolves a Kaladim character's guild without needing per-deployment world config.

Backward compat: absent / null / empty `logger_server` → falls back to `EQ2_WORLD` env var as before. Older plugin versions and the local-ingest path keep working unchanged. The override path lives in `_resolve_uploader_guild_async(uploader, world=None, *, allow_census=False)`.

**Gzip uploads (plugin v0.1.16+, server 2026-07-28)**: `GzipRequestMiddleware` (backend/server/core/gzip_request.py, pure ASGI) transparently inflates any request with `Content-Encoding: gzip` before FastAPI parsing and the HMAC check see the body — the signature contract stays "HMAC over the uncompressed JSON" for both formats, and plain uploads pass through untouched (old plugins unaffected). 16 MiB decompressed cap (413), bad gzip → 400. Rationale: 282 KB ACT payloads on a ~120 kbit/s throttled route couldn't finish inside the plugin's HttpClient timeout; gzip is 10–20×.

**Zero-Census response path (2026-07-28)**: the ingest handler never awaits Census before responding. Guild resolve is cache→census_store only (any age); a never-seen uploader returns CENSUS_UNAVAILABLE → commit with `guild_name=NULL` → `_backfill_encounter_guild` (BackgroundTasks, `allow_census=True`) does the live lookup + roster prewarm after the response. Combatant snapshots were already cache-only inline with background fill. Rationale: the plugin's HttpClient timeout is 20 s ([UploadClient.cs:52](https://github.com/VortexUK/EQ2LexiconACTPlugin/blob/main/src/Core/UploadClient.cs)) and one degraded inline Census call blew it — the upload "failed" client-side while the server committed anyway.

**HMAC payload signing (v0.1.8+ plugin, server-side validator added 2026-05-25, strict mode same day)**:

Plugin computes `HMAC-SHA256(body_bytes, api_token)` and ships it as `X-Lexicon-Signature` (lowercase hex). Server reads the bearer token from the Authorization header, recomputes the HMAC over `await request.body()`, and `hmac.compare_digest`s against the header. Mismatch → 401.

Runs in **strict** mode (`_validate_payload_signature`): on token auth the header is required — absence is a 401 whose `detail` includes the releases URL so the user knows to update. Browsers (session-cookie auth) skip validation since they have no token-style key, but sending the header from a session client is a 400 (confused client > silent accept). The rollout went straight to strict because the user base is small and all pre-alpha; if you ever need an opportunistic-mode reintroduction, restore the `if not sig_header: return` early-out under a feature flag.

Threat model: the legitimate token holder can sign anything — this doesn't stop a user forging their own parse. What it does stop is (a) casual payload tampering by editing JSON in a debugging proxy, (b) MITM mutation of the body in flight, (c) replay using only a stolen token without the protocol knowledge to sign. Real integrity has to come from server-side sanity checks (DPS-vs-level caps, plausible encounter duration, cross-validation) layered on top.

**Raid-only client filter (2026-09-30)**: `GET /api/zones/raid-bosses` (public, 30/min, `Cache-Control: max-age=3600`) returns `{version, bosses[]}` — every curated raid-zone (`raid_x4`/`raid_x2`) mob name normalised with the rankings' `_normalise_boss_key` (lowercase, NFC, apostrophe variants collapsed), `version` = content hash. Built by `rankings._raid_boss_names` (lru, cleared by `invalidate_zones_cache`). EQ2Parser's "Upload only raid fights" option syncs it and uploads a fight when its title is on the list OR it had 7+ player allies (the rankings' raid scope). Keep the normaliser and the 7-player floor in step with `_SCOPES` / `_normalise_boss_key`.

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
- **Registry**: the `servers` table in the users schema maps subdomain → world name + per-server settings (`max_level`, `current_xpac`, `launch_dt`, `display_name`). The registry is loaded into memory at startup via `load_registry()` and re-read after admin edits.
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

The 2026-05-29 cleanliness audit introduced a set of canonical primitives, hooks, and utilities. When writing new frontend code, reach for these BEFORE rolling your own — every hand-rolled version diverges and accrues drift.

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
| `ENV` | `production` on Railway (set 2026-10-07). Only a dev-style value (`dev`/`development`/`local`/`test`) enables the `X-Server` header / `?server=` tenant override in `server_context.py`; unset == override closed. Tests set `dev` in conftest. |
| `SESSION_COOKIE_DOMAIN` | Parent domain for the session cookie so one login spans both subdomains (e.g. `.eq2lexicon.com` in prod). Leave unset in dev. |
| `SERVER_CURRENT_XPAC` | Seed-only — runtime value is per-server in the `servers` table (admin-editable). Seeds the current expansion for the Varsoon row on first migration. |
| `SERVER_MAX_LEVEL` | Seed-only — runtime value is per-server in the `servers` table (admin-editable). Seeds the max character level for the Varsoon row on first migration. |
| `LAUNCH_DT` | Seed-only — runtime value is per-server in the `servers` table (admin-editable). Seeds the server launch datetime for the Varsoon row on first migration. |
| `ADMIN_DISCORD_IDS` | Comma-separated Discord IDs allowed to hit `/api/admin/*` and delete arbitrary parses |
| `DATABASE_URL` | Postgres DSN for the writable families (users/parses/census/zones/raids — one Supabase database, five schemas). Resolution chain: `DATABASE_URL` → `SUPABASE_DB_URL` → `POSTGRES_CONNECTION_STRING` (`backend/pg.py`). Production = the Supabase SESSION pooler (`:5432`, `sslmode=require`); never the transaction pooler (`:6543`). Migrations in `db/migrations/` apply at app startup. |
| `TEST_DATABASE_URL` | Local PostgreSQL 17 the pytest suite provisions and runs against (default `postgresql://postgres:postgres@localhost:5432/eq2lexicon_test`; created automatically). See `tests/fixtures/pg.py`. |
| `PARSE_RETENTION_DAYS` | Days after the fight before the retention sweep (`backend/server/parses/cleanup.py`) hard-deletes a trash parse and collapses a named fight's duplicate uploads to its primary. Default `3`. |
| `PARSE_DETAIL_RETENTION_RAID_DAYS` / `_DUNGEON_DAYS` / `_OTHER_DAYS` | Tiered breakdown-detail retention (defaults 30/14/7): days before a kept fight's `attack_types`+`damage_types` are dropped, by zone category (curated raid / curated group-instance / other). Summary rows live forever. |
| `INGEST_CLIENT_LIMITS` | Per-client-app flood protection on `/api/parses/ingest` (`backend/server/core/client_throttle.py`): `<User-Agent prefix>=<limit>` pairs, `;`-separated, matched case-insensitively as a prefix, bucketed per (app, uploader). Default `EQ2AdvancedDesktop=30/hour` — the third-party client that retried a 500 every ~2 s for nine hours on 2026-09-27. Unlisted clients unaffected; empty disables. |
| `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` | Twitch app (client-credentials) creds for the raid-schedule "Raiding live" list (`backend/server/raid_live.py`). Both optional — unset ⇒ live list disabled, raid schedule unaffected. |
| `DB_ITEMS_PATH` / `DB_SPELLS_PATH` / `DB_RECIPES_PATH` | **Retired** (Phases 2-3, 2026-10) — every data family lives in a Postgres schema (`DATABASE_URL`); the Railway volume is gone and no `DB_*_PATH` var does anything (`DB_AAS_PATH` included — aas/classes are migration-seeded, 0011/0012). Catalogue refreshes are script runs (`scripts/download_*.py`, `scripts/build_aas_db.py`) against the DB. |
| `DB_CLASSES_PATH` | **Ignored** (warning logged if set) — class data lives in the Postgres `classes` schema, seeded by `db/migrations/0011_classes.sql` (Phase 3). The seeds are canonical and carry `census_classid` separately from `icon_id` (the 2026-08 Coercer/Illusionist prod bug was that conflation). |
| `R2_ENDPOINT` / `R2_BUCKET` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | Cloudflare R2 bucket for the nightly `pg_dump` backups — set as GITHUB ACTIONS secrets (`.github/workflows/pg-backup.yml`), not Railway vars (litestream is retired). |

## Census API patterns

Base URL: `https://census.daybreakgames.com/s:{service_id}/json/get/eq2/`
- Item: `item/?displayname=<name>` / `item/?id=<id>` — game-link IDs are signed 32-bit; convert negatives with `+= 2**32`
- Guild: `guild/?name=<name>&world=<world>&c:resolve=members(...)&c:show=member_list,name,world,rank_list`
- Character spells: `character/?name.first=<name>&locationdata.world=<world>&c:resolve=spells(name,tier_name,type,level,given_by)&c:show=name,spell_list`
- Character AAs: `character/?name.first=<name>&locationdata.world=<world>&c:show=name,alternateadvancements` — response has `alternateadvancements.alternateadvancement_list[]{tier, treeID, id}` where `id` == `nodeid` in tree JSON

## AA tree notes

Tree + node + per-xpac-limit reference data lives in the Postgres `aas` schema (see `backend/eq2db/aas.py`); migration `db/migrations/0012_aas.sql` carries the FULL dataset as seeds, so every environment is data-complete from migrations alone. Refresh: `scripts/download_aa_trees.py` fetches tree JSONs locally (gitignored intermediates), `scripts/build_aas_db.py` upserts them + the committed `aa_limits.json` into the schema at DATABASE_URL. Icons stay as files: `data/AAs/icons/{id}.png` served at `/aa-assets`. `bg_sprite.png` has backdrop circles (44px) and badge circles (24px) — see `backend/image/aa_tree.py` for exact offsets.

`tree_type` is precomputed at build time (`aas.detect_tree_type`): `class`, `subclass`, `shadows`, `heroic`, `tradeskill`, `tradeskill_general`, `warder`, `prestige`, `dragon`, `reign_of_shadows`, `far_seas`, or `unknown`. The last six fall back to `render_subclass_tree` pending calibration. Coordinate systems are native 640×480 (SCALE=2 → 1280×960); full arithmetic is in `backend/image/aa_tree.py`. The `/aacheck` command offers five static tree choices (Class/Subclass/Shadows/Heroic/Trade) to avoid autocomplete API calls.

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

### Backups (Postgres → Cloudflare R2, nightly pg_dump)

Every data family lives in Supabase Postgres; `.github/workflows/pg-backup.yml` runs a nightly `pg_dump -Fc` of all ten family schemas (items/spells/recipes are not re-seedable without a multi-hour Census crawl) into the R2 bucket (`pgdump/` prefix) with 30-day in-action pruning. Secrets live on the GitHub repo: `SUPABASE_DB_URL` (session pooler) + the four `R2_*` values. Restore drill:

```bash
pg_restore -d "$DATABASE_URL" --clean --if-exists eq2lexicon-<ts>.dump
```

(The litestream/SQLite replication era is over; its R2 prefixes can be deleted ~2 weeks after a healthy cutover. The one-time R2 token how-to: dash.cloudflare.com → R2 → API tab → Create API Token — NOT "Account API tokens" — Object Read & Write on the bucket.)

### Zone metadata refresh (zones schema)

Zone metadata is built from the cleaned wiki dump straight into Postgres — no file upload. Boss rosters are **web-editable** curator data; a rebuild upserts `zones` by stable id and fully replaces `zone_types`/`zone_aliases`, never touching `zone_encounters` / `zone_encounter_mobs` (removed zone names ARE pruned, cascading their rosters — the script prints the count; a renamed source zone reads as new+pruned, so check that output).

```powershell
python scripts/dev/clean_eq2_zones.py     # source → cleaned JSON
uv run python scripts/build_zones_db.py   # cleaned JSON → the zones schema (DATABASE_URL)
uv run python scripts/dev/_smoke_test_zones_db.py
```

### Raid strategies seed (raids schema)

Raid strategies are hybrid — wiki-seeded plus user edits + revision history.

1. **First-time seed** (once, before any production user edits):

   ```powershell
   python scripts/dev/scrape_eq2i_raids.py --all-raids
   uv run python scripts/dev/ingest_raids_json.py --in scripts/dev/eq2_raid_data.json
   ```

   Writes into the raids schema at `DATABASE_URL`.

2. **Refreshing scraped content**: re-run the scrape, then the ingest. `upsert_raid_encounter` skips `SOURCE_MANUAL` rows — human edits survive every re-scrape.

## Logging conventions

- **Module-level binding**: `_log = logging.getLogger(__name__)`.
- **Lazy `%s` formatting**: `_log.info("foo %s", x)` — never f-strings inside log calls.
- **Bracketed prefix per module**: `[lowercase-with-hyphens]` (e.g. `[cache]`, `[census-refresh]`). Helps grep by component.
- **For audit events**: use `audit_log("snake_case_action", actor=..., **fields)` from `backend.server.core.audit_log`. Don't hand-roll `_log.info("[audit] …")`.
- **Levels**: WARNING for security signals (HMAC mismatch, invalid token); INFO for audit-trail / startup config / state changes; DEBUG for per-request noise; ERROR/exception for "needs investigation". Recoverable Census flakes are WARNING, not ERROR (per the 2026-05-30 audit).
- **Env vars**: `LOG_LEVEL` (default `INFO`) and `LOG_FORMAT` (`text` default, `json` for Railway) are read by `configure_logging()` in `backend/core/logging_config.py` at startup.
- **Sensitive values that NEVER appear inside a log-call argument list**: bearer tokens, HMAC signature bytes, `DISCORD_CLIENT_SECRET`, OAuth `access_token`s, the `raw` token from `mint_api_token`.
- **Floods produce one line, not one per request** (2026-09-28, after a third-party client's ~2,000 tracebacks/hour buried the audit trail): an unhandled exception is caught by the outermost `UnhandledErrorMiddleware` (`core/unhandled_errors.py`) → one ERROR line (method, path, leaf exception, deepest frame in our code, request_id) + JSON 500, full traceback only once per (path, exception) per 5 min via `core/log_coalesce.py`. 429s log once per (client, path) per minute in `app.py:_rate_limit_handler` (slowapi's own per-request warning is pinned to ERROR). Per-client-app ingest budgets live in `core/client_throttle.py` (`INGEST_CLIENT_LIMITS`). A client-side payload bug (duplicate rows) is a 422 with a WARNING naming the client, never a 500. Before adding a per-request log line, ask what it looks like at 3,600/hour — if the answer is "noise", key it through the coalescer.

## Frontend design principles

When building or changing the React frontend, hold to these — the goal is a distinctive, cohesive interface that reads as *deliberately designed for an EverQuest 2 guild tool*, not a generic dashboard. (Implementation rules live in [Frontend styling — Tailwind v4](#frontend-styling--tailwind-v4-enforced) above; these are the aesthetic intent behind them.)

- **Typography**: Use characterful, intentional fonts. Headings are **Cinzel** (`font-heading`) — a classical serif fitting Norrath's high-fantasy tone; body is **Spectral** (`font-body`), a screen serif that reinforces the "lexicon/tome" voice. In-game-style tooltips deliberately use Times New Roman to mirror EQ2's client. Never introduce generic UI fonts (Inter, Roboto, Arial, system fonts) for display text.
- **Color & theme**: One cohesive palette — gold (`--color-gold`) on deep stone/parchment. Gold is the single accent (links, focus, active states); Discord blurple is confined to the sign-in button only. Dominant base colours with sharp metallic accents beat timid, evenly-distributed palettes. Avoid the clichéd purple-gradient-on-white "AI slop" look.
- **Motion**: Favour CSS-only transitions. Spend the budget on a few high-impact moments (the staggered page-load reveal via `.page-enter`) rather than scattering small effects; honour `prefers-reduced-motion`.
- **Backgrounds & depth**: Atmosphere via the layered background overlay (warm top-glow + vignette) and the gilded card treatment (gold-tinted edge, soft shadow, top hairline) — not flat fills. Keep it legible.
- **Cohesion over novelty**: Every page should feel part of the same product. Reuse the theme utilities and the `ui/` primitives; never reinvent spacing, card, or button styles per page.
