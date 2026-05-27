# CLAUDE.md — EQ2Lexicon

## What this project is

A Discord bot and web companion site (FastAPI + React/TypeScript) that queries the EverQuest 2 Daybreak Census API. The Discord bot provides slash commands: item tooltip images, guild member tables, character spell summaries, and AA tree visualisations. The web companion provides character sheets, spell tabs with tier pip icons, AA tree tabs, item tooltips, Discord login, and character claiming. Deployed on Railway; git push to `main` triggers redeploy.

## Key files

| File | Purpose |
|---|---|
| `census/client.py` | All Census API HTTP calls. `CensusClient` has `get_item`, `get_guild`, `get_character_spells`, `get_character_aas`, `get_raw_item`. |
| `census/models.py` | Dataclasses: `ItemData`, `ItemStat`, `ItemEffect`, `GuildData`, `GuildMember`, `CharacterSpells`, `SpellEntry`, `NodeAA`, `CharacterAAs` |
| `census/constants.py` | `STAT_MAP` (stat display names/groups), class frozensets (`FIGHTERS`, `PRIESTS`, `SCOUTS`, `MAGES`, `ARTISANS`), `ARCHETYPES`, `CLASS_GROUPS`, `TYPEINFO_DISPLAY`, `ITEM_DISPLAY` |
| `census/item_parser.py` | Item data parsing (parse_item, parse_stats, parse_effects, parse_flags, _armor_type, _slot_type, _fmt_duration, parse_set_bonuses) extracted from client.py |
| `census/spells_db.py` | Local SQLite spell catalogue: strip_roman, unique_highest_entries, load_blocklist, find_by_ids, find_by_crc (@lru_cache maxsize=4096), spell_to_row, upsert_spells |
| `census/recipes_db.py` | Local SQLite recipe catalogue (~70k rows): recipe_to_row, upsert_recipes, find_by_id, find_by_name, find_by_output_id. Secondary components stored as JSON array. Download with scripts/download_recipes.py |
| `census/zones_db.py` | Local SQLite zone catalogue (~1124 rows). Four tables: `zones` (canonical record + expansion attribution + classification flags), `zone_types` (many-to-many type tokens — `solo`/`group`/`raid_x4`/etc.), `zone_aliases` (alias→canonical for ACT log fuzziness), `zone_bosses` (raid boss list per zone, sourced from EQ2i scrape). Lookup helpers: `find_by_name` (canonical OR alias, includes bosses array), `list_by_expansion(short, type_filter=None)`, `list_by_event`, `list_by_type`, `list_bosses_for_zone`, `find_zones_by_boss`. Sourced from `scripts/dev/eq2_zones.cleaned.json` + `scripts/dev/eq2_raid_data.json`; rebuild via `scripts/build_zones_db.py`. |
| `census/wikitext_md.py` | MediaWiki wikitext → markdown converter using `mwparserfromhell`. Handles EQ2i-specific templates (`{{Monster}}`, `{{loc}}`, `{{IZoneInformation}}`), wikilinks → markdown links with EQ2i base URL, nested lists, headings, bold/italic. Used by the raid scraper and (future) the strategy editor preview. |
| `census/raids_db.py` | Local SQLite raid-strategy catalogue. Schema: `raid_zones` + `raid_encounters` (one markdown blob per encounter for PoC) + `raid_encounter_revisions` (version history). Companion to `zones_db.py` — strategies are human-edited and revision-tracked, while the boss LIST lives in `zones_db.zone_bosses`. RAIDS_DB_PATH env var. |
| `image/tooltip.py` | PIL renderer for item tooltips. Renders at 2× then downsamples (SCALE=2, ZOOM=1.3). Width is `round(368 * ZOOM)`. |
| `image/aa_tree.py` | AA tree renderers and coordinate systems. See AA tree notes below. |
| `bot/bot.py` | Registers all cogs, syncs slash commands to three specific guild IDs (648253204760625160, 955890381847928892, 1502314690041221260) for instant propagation plus a global sync. |
| `bot/cogs/items.py` | `/item` — accepts name, numeric ID, or game link |
| `bot/cogs/guild.py` | `/guild` — tabular member list sorted by rank then level |
| `bot/cogs/spellcheck.py` | `/spellcheck` — spell tier summary or full list (`details:True`) |
| `bot/cogs/aacheck.py` | `/aacheck` — renders a character's AA tree with tier badges |
| `web/config.py` | Single source of truth: SERVICE_ID, WORLD from env vars |
| `web/cache.py` | TTLCache with stale-while-revalidate: character_cache, guild_cache, claim_cache |
| `web/routes/aa.py` | GET /api/character/{name}/aas — AA profile list with per-tree data |
| `web/routes/characters.py` | GET /api/characters/search — character name search |
| `web/routes/guild_officer.py` | Officer claim-review endpoints; imports _officer_chars, _roster_rank_map from guild.py |
| `web/routes/item_watch.py` | Item watch endpoints; imports _officer_chars, _roster_rank_map from guild.py |

## ACT plugin upload (`POST /api/parses/ingest`)

The [EQ2LexiconACTPlugin](https://github.com/VortexUK/EQ2LexiconACTPlugin) sends each finished encounter here as an ACT-shaped JSON payload (`web/routes/parses.py:ingest_parse`). Bearer-token auth via `require_user_session_or_token`.

**`logger_server` field (plugin v0.1.10+ , server-side override added 2026-05-25)**:

Plugin auto-detects the EQ2 server from its log file path (`<install>/logs/<server>/eq2log_<character>.txt`) and stamps it as `logger_server` on every upload. The server uses it to override `EQ2_WORLD` for the Census guild lookup — so a Varsoon-configured deployment correctly resolves a Kaladim character's guild without needing per-deployment world config.

Backward compat: absent / null / empty `logger_server` → falls back to `EQ2_WORLD` env var as before. Older plugin versions and the local-ingest path keep working unchanged. The override path lives in `_resolve_uploader_guild_async(uploader, world=None)`.

**HMAC payload signing (v0.1.8+ plugin, server-side validator added 2026-05-25, strict mode same day)**:

Plugin computes `HMAC-SHA256(body_bytes, api_token)` and ships it as `X-Lexicon-Signature` (lowercase hex). Server reads the bearer token from the Authorization header, recomputes the HMAC over `await request.body()`, and `hmac.compare_digest`s against the header. Mismatch → 401.

Runs in **strict** mode (`_validate_payload_signature`): on token auth the header is required — absence is a 401 whose `detail` includes the releases URL so the user knows to update. Browsers (session-cookie auth) skip validation since they have no token-style key, but sending the header from a session client is a 400 (confused client > silent accept). The rollout went straight to strict because the user base is small and all pre-alpha; if you ever need an opportunistic-mode reintroduction, restore the `if not sig_header: return` early-out under a feature flag.

Threat model: the legitimate token holder can sign anything — this doesn't stop a user forging their own parse. What it does stop is (a) casual payload tampering by editing JSON in a debugging proxy, (b) MITM mutation of the body in flight, (c) replay using only a stolen token without the protocol knowledge to sign. Real integrity has to come from server-side sanity checks (DPS-vs-level caps, plausible encounter duration, cross-validation) layered on top.

## Web companion architecture

FastAPI backend + React/TypeScript frontend. Key design decisions:

- **Single env config**: `web/config.py` exports `SERVICE_ID` and `WORLD`; all web routes import from there (not `os.getenv` directly).
- **Stale-while-revalidate cache**: `web/cache.py` TTLCache returns stale data immediately and fires a background refresh; hard-expires after 1 hr.
- **Circular import avoidance**: `_overview_to_char_response` in `guild.py` uses a local import of `_build_char_response` from `character.py` inside the function body.
- **Route split**: Large guild.py split into `guild.py` (roster + spellcheck + adorn), `guild_officer.py` (officer claim review), `item_watch.py` (item watch).
- **Frontend split**: `CharacterPage.tsx` exports `StatGroup`/`StatRow`; `CharacterAAsTab.tsx` and `CharacterSpellsTab.tsx` import them.
- **Spell icons**: served as static files at `/spell-icons/{id}.png`; backdrop + foreground layered with CSS `position: absolute; inset: 0`.

## Frontend styling — Tailwind v4 (ENFORCED)

Tailwind v4 is the **single** styling system. There is no `tailwind.config.js` and no PostCSS — config is CSS-first in `frontend/src/index.css` via `@theme`. New frontend work MUST follow these rules; do not reintroduce the old patterns.

**The one rule:** Tailwind **utility classes for all static styling**; `style={{…}}` **only** for runtime-computed/dynamic values (data-driven colours, computed widths/positions, `gridTemplateColumns`, gradient-text, glows). Do **not** add new static inline `style` objects, and do **not** create per-page `CSSProperties` style-object consts.

- **Tokens → utilities**: design tokens live in the `@theme` block (`--color-*`, `--font-*`, `--radius-*`) and generate utilities — `bg-surface`, `text-gold`, `text-text-muted`, `border-border`, `text-rarity-fabled`, `font-heading`, `rounded-md`, etc. Spacing uses Tailwind's built-in 4px scale (`p-4` = 1rem). Use arbitrary values (`text-[0.88rem]`, `py-[0.45rem]`) only when no token/step fits.
- **Cascade layers**: `@layer base` (reset + element defaults) → `@layer components` (the `.btn`/`.card`/nav classes) → `utilities` (last, so page utilities win). Tailwind **Preflight is intentionally NOT imported** — the app has its own reset; keep it that way.
- **Primitives**: use `<Button>` / `<Card>` / `<SectionLabel>` from `frontend/src/components/ui` for buttons, surface panels, and the uppercase gold eyebrow. Don't hand-roll a styled `<button>`/card `<div>`.
- **Rarity/tier colours**: ONE source of truth — `frontend/src/rarityColors.ts` (`itemRarityColor`, `recipeTierColor`, `qualityStyle`) backed by the `--color-rarity-*` tokens. Never define a new `TIER_COLOUR` map in a page.
- **Legacy `var(--*)` aliases**: `:root` still aliases the old names (`--gold` → `var(--color-gold)`, etc.) so the remaining *dynamic* `style={{}}` values resolve. Fine to reference in `style` for dynamic values; for static styling use the utility instead.
- **Exceptions (keep bespoke inline)**: `ItemTooltip`, `SpellScrollTooltip`, `AATree` faithfully recreate the in-game client (Times New Roman, computed glows/positions) — leave their inline styling alone.

## Environment variables

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token from Discord developer portal |
| `CENSUS_SERVICE_ID` | Census API service ID (default `example`, rate-limited) |
| `EQ2_WORLD` | EQ2 server name used for guild/spellcheck/aacheck lookups (default `Varsoon`) |
| `SERVER_CURRENT_XPAC` | Expansion the rankings page defaults its Expansion selector to — accepts the short code (`EoF`) or the full name (`Echoes of Faydwer`), case-insensitive. Falls back to the most recent expansion that has raids in zones.db if unset or unknown. |
| `ADMIN_DISCORD_IDS` | Comma-separated Discord IDs allowed to hit `/api/admin/*` and delete arbitrary parses |
| `USERS_DB_PATH` | Override the default `data/users.db` location (set on Railway to the persistent-volume mount) |
| `PARSES_DB_PATH` | Override the default `data/parses/parses.db` location (set on Railway to the persistent-volume mount) |
| `ZONES_DB_PATH` | Override the default `data/zones/zones.db` location. Set on Railway to the persistent-volume mount (the `.db` itself is not committed — uploaded manually; see "Manual upload: zones.db" below). |
| `R2_ENDPOINT` | Litestream backups → `https://<account>.r2.cloudflarestorage.com` |
| `R2_BUCKET` | Litestream backups → bucket name (e.g. `eq2lexicon-backups`) |
| `R2_ACCESS_KEY_ID` | Litestream backups → R2 API token Access Key ID |
| `R2_SECRET_ACCESS_KEY` | Litestream backups → R2 API token Secret Access Key |

## Census API patterns

Base URL: `https://census.daybreakgames.com/s:{service_id}/json/get/eq2/`

- Item by name: `item/?displayname=<name>&c:limit=1`
- Item by ID: `item/?id=<id>&c:limit=1`
- Item by game link: extract signed int from `\aITEM <id>`, convert negative to unsigned (`+= 2**32`), then use ID lookup
- Guild: `guild/?name=<name>&world=<world>&c:resolve=members(...)&c:show=member_list,name,world,rank_list&c:limit=1`
- Character spells: `character/?name.first=<name>&locationdata.world=<world>&c:resolve=spells(name,tier_name,type,level,given_by)&c:show=name,spell_list&c:limit=1`
- Character AAs: `character/?name.first=<name>&locationdata.world=<world>&c:show=name,alternateadvancements&c:limit=1`
  - Response has `alternateadvancements.alternateadvancement_list` with entries `{tier, treeID, id}` where `id` matches `nodeid` in the tree JSON

## AA tree notes

### Data files (`data/AAs/`)
- `trees/{id}.json` — one file per tree, contains `alternateadvancement_list[0]` with `name`, `ofyclassification`, and `alternateadvancementnode_list`
- Each node has: `nodeid`, `xcoord`, `ycoord`, `icon.id`, `icon.backdrop`, `maxtier`, `classification`
- `icons/{id}.png` — node icon images downloaded from Census
- `bg_sprite.png` — sprite sheet: 7 backdrop circles (44px, ids -1/456–461) then 3 badge circles (24px: white/yellow/green)
  - Backdrop x-offsets: `{-1:0, 456:45, 457:90, 458:135, 459:180, 460:225, 461:270}`
  - Badge x-offsets: yellow (not maxed) = 340, green (maxed) = 365

### Tree type detection (`detect_tree_type`)
Detects from xcoord sets, max ycoord, `ofyclassification`, and node `classification` strings. Returns one of: `class`, `subclass`, `shadows`, `heroic`, `tradeskill`, `tradeskill_general`, `warder`, `prestige`, `dragon`, `reign_of_shadows`, `far_seas`, `unknown`.

### Coordinate systems (native 640×480 base, rendered at SCALE=2 → 1280×960)
- **class**: columns at x=86,206,327,447,567 for xcoords 1,4,7,10,13; rows at y=42+(ycoord×66.67)
- **subclass**: anchor x=234 at xcoord 15, step 155/12 px/unit; y=42+(ycoord×21.05), ycoords 0–19
- **shadows**: native 632×472; x=40+(xcoord×13) scaled by IMG_W/632; y from `{1:59,6:166,11:273,16:377}` scaled by IMG_H/472
- **heroic**: x=65+((xcoord-2)×13), y=50+((ycoord-1)×22); no overlay
- **tradeskill**: x=65+((xcoord-2)×13), y=60+((ycoord-1)×21); no overlay

### `/aacheck` command
- Five static choices: Class/Subclass/Shadows/Heroic/Trade (avoids repeated API calls for autocomplete)
- At runtime: fetches character AAs, iterates their tree IDs, matches by `detect_tree_type` result, renders with `aa_data: dict[node_id → tier]`
- Badge: yellow if `tier < maxtier`, green if `tier >= maxtier`; positioned bottom-right of node (32px output, slight overlap)
- Caption shows real tree name (e.g. "Templar") and total points spent

### Unimplemented tree types
`tradeskill_general`, `warder`, `prestige`, `dragon`, `reign_of_shadows`, `far_seas` all fall back to `render_subclass_tree` pending proper calibration.

## Tooltip rendering notes

- Quality tier colours: Fabled = pink `(255,153,255)` with pink glow, Legendary = `(255,201,147)` orange glow, Treasured/Mastercrafted = `(147,217,255)` orange glow, Uncommon/Handcrafted/Common = `(190,255,147)` no glow
- Primary stats (green `#22ff22`): Stamina, Primary Attributes, Resistances, Combat Skills
- Secondary stats (cyan): everything else
- Stat ordering controlled by `_PRIMARY_ORDER` dict in `tooltip.py`
- Class list collapsed via `CLASS_GROUPS` exact match first, then `ARCHETYPES` decomposition
- Extra info rows (Type, Slot, Mitigation, Level, Charges, Duration, etc.) are config-driven via `ITEM_DISPLAY` and `TYPEINFO_DISPLAY` in `constants.py`
- Adornments show "Adds the following to an item:" header when `armor_type` contains "adornment"

## Guild command notes

- Members without a `type` dict in the API response are filtered out (incomplete data)
- Rank is a numeric ID in `member["guild"]["rank"]`; resolved to name via `rank_list` from the guild response
- Columns: Rank, Name, Class (Level), AA, Tradeskill (Level), Deity
- Sorted by rank ID ascending, then level descending
- Sends as `.txt` file attachment if table exceeds 2000 chars

## Spellcheck command notes

- Deduplication: strips trailing Roman numerals (I–XX) to get base name, keeps highest-level entry per base name per type
- `details:True` flag shows all individual spells grouped by tier, ordered by level

### Discord bot filter (`/spellcheck`)
- level > 0, type in (spells, arts), given_by NOT IN (alternateadvancement, class)
- Blocklist applied via `load_blocklist()` (re-read each call from data/spells/blocklist.json)

### Web endpoint filter (`/api/character/{name}/spells`)
- level > 0, type in (spells, arts), given_by == 'spellscroll'
- given_by='spellscroll' covers both mage spells and fighter/scout combat arts once scribed
- given_by='class' entries are auto-granted fixed-tier abilities (Invisibility, base combat art ranks etc.) — excluded
- Blocklist applied via `load_blocklist()`

## Spell blocklist

`data/spells/blocklist.json` holds base spell names (no Roman numerals) to suppress:
```json
{ "blocked": ["Fighting Chance"] }
```
- `load_blocklist()` in `spells_db.py` re-reads the file on every call
- Applied in both the web `/spells` endpoint and the Discord `/spellcheck` cog
- Add to `blocked` and the change takes effect without a restart

## Local testing scripts

```
python scripts/preview_item.py "Faded Black Hood"
python scripts/inspect_item.py "Faded Black Hood"       # raw JSON dump
python scripts/preview_guild.py "Exordium"
python scripts/preview_spellcheck.py Sihtric
python scripts/preview_spellcheck.py Sihtric --details
python scripts/preview_spellcheck.py Sihtric --debug    # shows each counted spell
python scripts/preview_aa_tree.py 25                    # render tree ID 25
python scripts/preview_aacheck.py Menludiir             # list character's AA trees
python scripts/preview_aacheck.py Menludiir Templar     # render by tree name (partial match)
python scripts/download_aa_trees.py                     # re-download all tree JSONs
python scripts/download_aa_icons.py                     # re-download all node icons
python scripts/download_spells.py --guild "Guild Name"  # seed spell cache DB for a guild
python scripts/download_spells.py --guild "Guild Name" --refresh  # force re-fetch all
python scripts/download_spell_icons.py               # download all spell icon PNGs
python scripts/download_spell_icons.py --start N     # resume from icon N
python scripts/download_recipes.py                   # download all ~70k recipes into data/recipes/recipes.db
python scripts/download_recipes.py --limit 500       # test run (500 recipes)
python scripts/download_recipes.py --restart         # ignore saved offset, re-download from scratch
python scripts/dev/clean_eq2_zones.py                # re-clean scripts/dev/eq2_zones.json → eq2_zones.cleaned.json
python scripts/build_zones_db.py                     # build data/zones/zones.db from the cleaned JSON
python scripts/dev/_smoke_test_zones.py              # validate the cleaned JSON
python scripts/dev/_smoke_test_zones_db.py           # validate the built SQLite DB
```

## Deployment

- Platform: Railway, Nixpacks builder
- Push to `main` branch triggers redeploy
- New slash commands may take up to 1 hour to propagate globally, but appear instantly in the registered guild IDs above
- Do not push until the user confirms local testing passes

### Backups (parses.db + users.db → Cloudflare R2)

[litestream](https://litestream.io) replicates both SQLite DBs to R2 in near-real-time. On container start, `litestream restore -if-replica-exists` rehydrates either DB from R2 if the Railway volume is wiped. After that the long-running `litestream replicate -exec` keeps both DBs in sync (RPO ~10 s, retention 7 days, snapshot every 24 h). Config lives in `litestream.yml`; the orchestration in `railway.toml`'s `startCommand`.

**One-time R2 setup:**

1. Sign in at https://dash.cloudflare.com → **R2**.
2. **Create bucket** — e.g. `eq2lexicon-backups`. Default region (auto-managed) is fine.
3. Note the **Account ID** from the R2 dashboard URL (or the bucket details page). The S3 endpoint is `https://<account_id>.r2.cloudflarestorage.com`.
4. **Manage R2 API Tokens → Create API Token**:
   - Name: `eq2lexicon-litestream`
   - Permissions: **Object Read & Write**
   - Specify bucket: the one you created
   - TTL: forever (or rotate quarterly)
5. Copy the **Access Key ID** and **Secret Access Key** (shown once).
6. In **Railway → Variables**, add:
   - `R2_ENDPOINT` = `https://<account_id>.r2.cloudflarestorage.com`
   - `R2_BUCKET` = `eq2lexicon-backups`
   - `R2_ACCESS_KEY_ID` = *(from step 5)*
   - `R2_SECRET_ACCESS_KEY` = *(from step 5)*
7. Next deploy starts streaming. Verify in R2 console — you should see `parses/` and `users/` prefixes appear within a minute.

**Manual restore** (e.g. testing recovery, or restoring a specific point-in-time):

```bash
# Restore latest from R2 into a chosen path (won't overwrite if file exists)
litestream restore -config /app/litestream.yml /app/data/parses/parses.db

# Restore an older snapshot
litestream restore -config /app/litestream.yml -timestamp 2026-05-24T18:00:00Z -o ./parses.db-snapshot /app/data/parses/parses.db
```

**Skipping backup before R2 is set up:** if the R2 env vars aren't populated, the startCommand's `|| true` makes the restore step a no-op and litestream's replicate-exec falls through cleanly (it just logs a "no replicas configured" warning per DB). The app still runs; you just don't have backups until the env vars land.

### Manual upload: zones.db

`data/zones/zones.db` is **read-only reference data** built from the cleaned wiki dump that lives in the repo. It's `.gitignore`d (it's a binary; rebuilding it is cheap and deterministic) and lives on the Railway persistent volume so it survives container restarts. Refresh procedure:

1. **Locally**, after any change to `scripts/dev/eq2_zones.json`, the overrides file, the aliases file, or the cleanup rules in `scripts/dev/clean_eq2_zones.py`:

   ```powershell
   python scripts/dev/clean_eq2_zones.py     # source → cleaned JSON
   python scripts/build_zones_db.py          # cleaned JSON → SQLite
   python scripts/dev/_smoke_test_zones.py
   python scripts/dev/_smoke_test_zones_db.py
   ```

2. **Upload** the resulting `data/zones/zones.db` to the Railway volume (drag-and-drop in the volume browser, or via `railway run` with a copy command).

3. **Set the env var** `ZONES_DB_PATH` to the absolute path on the volume, e.g. `/app/data/zones/zones.db`. (Defaults to that path already if you mount the volume at `/app/data` — env var only needed if your mount differs.)

4. **Verify** post-deploy with any code path that hits `zones_db.find_by_name(...)` — or curl an endpoint that reads it once one exists.

The DB has no migration story right now because everything is regenerated. If the schema in `census/zones_db.py` ever changes, just rebuild + re-upload — there's no user-data risk because zones.db carries no user-supplied rows.

## Frontend design principles

When building or changing the React frontend, hold to these — the goal is a distinctive, cohesive interface that reads as *deliberately designed for an EverQuest 2 guild tool*, not a generic dashboard. (Implementation rules live in [Frontend styling — Tailwind v4](#frontend-styling--tailwind-v4-enforced) above; these are the aesthetic intent behind them.)

- **Typography**: Use characterful, intentional fonts. Headings are **Cinzel** (`font-heading`) — a classical serif fitting Norrath's high-fantasy tone; body is **Spectral** (`font-body`), a screen serif that reinforces the "lexicon/tome" voice. In-game-style tooltips deliberately use Times New Roman to mirror EQ2's client. Never introduce generic UI fonts (Inter, Roboto, Arial, system fonts) for display text.
- **Color & theme**: One cohesive palette — gold (`--color-gold`) on deep stone/parchment. Gold is the single accent (links, focus, active states); Discord blurple is confined to the sign-in button only. Dominant base colours with sharp metallic accents beat timid, evenly-distributed palettes. Avoid the clichéd purple-gradient-on-white "AI slop" look.
- **Motion**: Favour CSS-only transitions. Spend the budget on a few high-impact moments (the staggered page-load reveal via `.page-enter`) rather than scattering small effects; honour `prefers-reduced-motion`.
- **Backgrounds & depth**: Atmosphere via the layered background overlay (warm top-glow + vignette) and the gilded card treatment (gold-tinted edge, soft shadow, top hairline) — not flat fills. Keep it legible.
- **Cohesion over novelty**: Every page should feel part of the same product. Reuse the theme utilities and the `ui/` primitives; never reinvent spacing, card, or button styles per page.

