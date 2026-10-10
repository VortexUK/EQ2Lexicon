---
paths:
  - "backend/census/**"
  - "backend/server/census_*.py"
  - "backend/server/cache.py"
  - "backend/server/guild_cache.py"
  - "backend/server/api/census.py"
  - "backend/server/api/stats.py"
  - "backend/server/api/characters.py"
---

# Census API, store, cache and refresh

## Census API patterns

Base URL: `https://census.daybreakgames.com/s:{service_id}/json/get/eq2/`
- Item: `item/?displayname=<name>` / `item/?id=<id>` — game-link IDs are signed 32-bit; convert negatives with `+= 2**32`
- Guild: `guild/?name=<name>&world=<world>&c:resolve=members(...)&c:show=member_list,name,world,rank_list`
- Character spells: `character/?name.first=<name>&locationdata.world=<world>&c:resolve=spells(name,tier_name,type,level,given_by)&c:show=name,spell_list`
- Character AAs: `character/?name.first=<name>&locationdata.world=<world>&c:show=name,alternateadvancements` — response has `alternateadvancements.alternateadvancement_list[]{tier, treeID, id}` where `id` == `nodeid` in tree JSON

## Architecture notes

- **Stale-while-revalidate cache**: `backend/server/cache.py` TTLCache returns stale data immediately and fires a background refresh; hard-expires after 1 hr.

## Files

| File | Purpose |
|---|---|
| `backend/census/client.py` | All Census API HTTP calls. `CensusClient` has `get_item`, `get_guild`, `get_character_spells`, `get_character_aas`, `get_raw_item`. |
| `backend/census/models.py` | Dataclasses: `ItemData`, `ItemStat`, `ItemEffect`, `GuildData`, `GuildMember`, `CharacterSpells`, `SpellEntry`, `NodeAA`, `CharacterAAs` |
| `backend/census/constants.py` | `STAT_MAP` (stat display names/groups), class frozensets (`FIGHTERS`, `PRIESTS`, `SCOUTS`, `MAGES`, `ARTISANS`), `ARCHETYPES`, `CLASS_GROUPS`, `TYPEINFO_DISPLAY`, `ITEM_DISPLAY` |
| `backend/census/item_parser.py` | Item data parsing helpers (parse_item, parse_stats, parse_effects, parse_flags, etc.) extracted from client.py |
| `backend/census/wikitext_md.py` | MediaWiki wikitext → markdown converter (mwparserfromhell). Handles EQ2i templates, wikilinks, nested lists, headings, bold/italic. Used by the raid scraper. |
| `backend/census/store.py` | Persistent store (Postgres `census` schema; characters + guilds keyed by name_lower + world, `data_json` jsonb) — `CensusStore(PgCatalogue)`, shared `store` instance (consumers alias it `census_store`). Keep-best-known merge — sparse Census refresh never nulls good data. Also `guild_history` (one row per guild per UTC day: level/members/accounts/achievements + max-level and distinct-class roster counts) written by `guild_cache._persist_and_publish_guild` via `upsert_guild_history` (prunes past `GUILD_HISTORY_RETENTION_DAYS`=400); served store-only by `GET /api/guild/{name}/history?days=` (never Census; empty list for an unseen guild) → the guild page History tab (`pages/guild/GuildHistoryTab.tsx`, lazy, Recharts in the `vendor-charts` chunk; `components/charts/GuildHistoryChart.tsx` — one axis per chart, fixed colour per series). Chunk naming caveat in `frontend/vite.config.ts`: rolldown puts React's CommonJS body in the alphabetically-first manual chunk, hence `core-react` (check `modulepreload` in dist/index.html after any manualChunks change). |
| `backend/server/cache.py` | TTLCache with stale-while-revalidate: character_cache, guild_cache, claim_cache. Character and guild read paths serve from `census_store` first and never block on Census. |
| `backend/server/api/characters.py` | GET /api/characters/search — character name search |
| `backend/server/api/stats.py` | Per-server Stats page from the census `character.stat` aggregate family (global/world/world×class rows, 12 lifetime stats as max/sum/avg, recomputed daily by census). `GET /api/stats/server` = population, class distribution, totals, named leaderboards (single-value stats census-sort; K/D sorts `.value` with a 1000-kill floor; the two-key hit records can't census-sort — range-filter near the aggregate max + client-side sort). `GET /api/stats/character/{name}` = lifetime panel w/ ability names via the spells schema `find_by_crc` (crc 4294967295 = sentinel, skip). classid = the classes schema `census_classid` (Templar=13), not `icon_id`. 6h SWR module cache per world; leader queries retry (census drops back-to-back sorted queries). Never builds inline: cold cache → 202 `{"status":"building"}` + background build (lock-deduped, frontend polls 5s); startup prewarm via `prewarm_server_stats()` in app.py lifespan; failed build → 60s cooldown → 503. `GET /api/stats/explore?stat=&cls=` = live top-20 by whitelisted stat (`_EXPLORE_STATS`: combat snapshots under `stats.combat.*`/health/power + progression scalars quests.complete / collections.complete / achievements.points/.completed / alternateadvancements.spentpoints, projected narrowly — never `c:show=achievements`, it drags the 500+-item list + lifetime `statistics.*`), optional class scope — census needs numeric `type.classid` (string `type.class` filters silently return empty); 15-min cache, stale-beats-error. |
| `backend/server/census_health.py` | Site-wide Census availability signal: background poll every 5 min; `is_down()`/`get_state()` read by the read/refresh paths. Flips to down only after `PROBE_FAILURES_TO_TRIP` (2) consecutive failed probes (the live breaker in `census/failures.py` covers real-request bursts); transitions log at WARNING (down) / INFO (up). |
| `backend/server/census_events.py` | In-process async pub/sub backing the SSE stream (single-process only). |
| `backend/server/census_refresh.py` | Background refresh orchestration (throttle 15 min / in-flight dedupe / skip-when-down); merges into census_store, updates cache, publishes SSE. `_merge_roster` best-known join. |
| `backend/server/api/census.py` | `GET /api/census/health` (first-paint snapshot) + `GET /api/census/stream` (SSE: character/guild refresh records + health changes). The stream is session-gated (401 anonymous — the login page's EventSource simply closes), capped at `MAX_STREAM_SUBSCRIBERS` (503), and filtered to the request's world: every refresh event carries `world`, and one process serves every subdomain. |

## Gotchas

- **Census returns character data only for recently active players.** Combatant snapshot resolution at parse ingest is therefore partial, and per-character boards that exclude unresolved classes look sparse. That is expected; do not chase the resolver.
- **Spell names before the 2010 revamp are absent from Census.** Servers in an earlier era log era-specific tier names; Census and the spells catalogue only carry the later "Name + numeral" form, so name matching between logs and Census is unreliable there. Pet abilities are logged possessively under the owner and never appear in a character's spell list.
- If an item seems missing from the items catalogue, search substrings before concluding it is absent; spelling drifts between sources, and live Census is the fallback authority.
