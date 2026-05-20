# CLAUDE.md — EQ2CensusBot

## What this project is

Two things in one repo:

1. **Discord bot** — slash commands for item tooltips (PNG), guild tables, spell tier checks, and AA tree renders. Deployed on Railway.
2. **Web companion site** — FastAPI backend + React frontend. Character sheets with full stat panels, paperdolls, HTML item tooltips, Discord OAuth login, and a character-claiming system with admin approval.

---

## Architecture overview

```
Discord bot:  main.py → bot/ + census/ + image/
Web site:     web/app.py (FastAPI) + frontend/ (React/Vite)
Shared:       census/client.py, census/models.py, census/db.py (item catalogue)
```

The web app has its own DB (`web/db.py` → `data/users.db`) for users and character claims, separate from the item catalogue (`census/db.py` → `data/items/items.db`).

---

## Key files

### Backend

| File | Purpose |
|---|---|
| `census/client.py` | All Census API HTTP. `CensusClient` methods: `get_item`, `get_character`, `get_guild`, `get_character_spells`, `get_character_aas` |
| `census/models.py` | Dataclasses: `ItemData`, `ItemStat`, `ItemEffect`, `EquipmentSlot`, `AdornSlot`, `CharacterOverview`, `GuildData`, `CharacterSpells`, `CharacterAAs` |
| `census/constants.py` | `STAT_MAP`, class frozensets, `ARCHETYPES`, `CLASS_GROUPS`, `TYPEINFO_DISPLAY`, `ITEM_DISPLAY` |
| `census/db.py` | Item catalogue SQLite (async via aiosqlite). `find_by_name`, `find_by_id`, `init_db`, `upsert_items`. Respects `SERVER_MAX_LEVEL` env var. |
| `web/db.py` | Users + character_claims SQLite. `init_db` (sync, called at startup), `upsert_user`, `get_active_claim`, `submit_claim`, `cancel_pending`, `list_claims`, `get_claim_by_id`, `review_claim` |
| `web/app.py` | FastAPI factory. Registers all routers, serves `frontend/dist/` in production, mounts `/icons` static dir. Calls `users_db.init_db()` on startup. |
| `web/routes/auth.py` | Discord OAuth2. Callback upserts user into DB. `/api/auth/me` returns user + `is_admin: bool` (from `ADMIN_DISCORD_IDS` env var). |
| `web/routes/character.py` | `GET /api/character/{name}` — Census first, no local cache. Uses `EQ2_WORLD` env var. |
| `web/routes/item.py` | `GET /api/item/{item_id}` — local item DB first, Census fallback. |
| `web/routes/claim.py` | `GET/POST/DELETE /api/claim` — character claim CRUD. `POST` validates character exists via Census before storing. |
| `web/routes/admin.py` | `GET /api/admin/claims`, `POST /api/admin/claims/{id}/approve`, `POST /api/admin/claims/{id}/reject`. Gated on `ADMIN_DISCORD_IDS`. |
| `image/tooltip.py` | PIL tooltip renderer. 2× supersampling (SCALE=2, ZOOM=1.3). Width = `round(368 * ZOOM)`. |
| `image/aa_tree.py` | AA tree renderers — class, subclass, shadows, heroic, tradeskill. |

### Frontend

| File | Purpose |
|---|---|
| `frontend/src/App.tsx` | React Router: `/`, `/character/:name`, `/claim`, `/admin` |
| `frontend/src/pages/HomePage.tsx` | Search bar + Discord login + claim status strip + admin link |
| `frontend/src/pages/CharacterPage.tsx` | Full character sheet. General banner, stats panel (left), paperdoll (right). Stat hover → highlights contributing item slots (direct = bright green, adorn-only = dim green). Prefetches all equipped item stats on load for the highlight feature. |
| `frontend/src/pages/ClaimPage.tsx` | Claim submit/status/change page. Handles: no claim, pending, approved, rejected states. |
| `frontend/src/pages/AdminPage.tsx` | Pending claim queue with inline approve/reject. Expandable history with status badges. |
| `frontend/src/components/ItemTooltip.tsx` | HTML item tooltip rendered as a fixed portal. Viewport-clamped using `useLayoutEffect`. Module-level cache (`_cache: Map<string, ItemDetail>`). Exports `getCachedItem` and `prefetchItem` for the stat-highlight feature. |
| `frontend/src/hooks/useAuth.ts` | Fetches `/api/auth/me`. Returns `{ status, user? }`. `user.is_admin: boolean`. |
| `frontend/src/hooks/useClaim.ts` | Fetches `/api/claim/me`. Returns `ClaimState` union + `refetch()`. |

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | Bot only | Discord bot token |
| `CENSUS_SERVICE_ID` | Both | Census API service ID (default `example`) |
| `EQ2_WORLD` | Both | EQ2 server name (default `Varsoon`) |
| `DISCORD_CLIENT_ID` | Web | OAuth2 app client ID |
| `DISCORD_CLIENT_SECRET` | Web | OAuth2 app client secret |
| `DISCORD_REDIRECT_URI` | Web | OAuth2 callback URL |
| `SESSION_SECRET` | Web | Secret for signing session cookies |
| `ADMIN_DISCORD_IDS` | Web | Comma-separated Discord IDs with admin access |
| `SERVER_MAX_LEVEL` | Both | Optional TLE level cap for item lookups |
| `ITEMS_DB_PATH` | Both | Override path for items.db |
| `USERS_DB_PATH` | Web | Override path for users.db |

---

## Census API patterns

Base URL: `https://census.daybreakgames.com/s:{service_id}/json/get/eq2/`

- Item by name: `item/?displayname=<name>&c:limit=1`
- Item by ID: `item/?id=<id>&c:limit=1`
- Game link: extract signed int from `\aITEM <id>`, convert negative to unsigned (`+= 2**32`)
- Character: `character/?name.first=<name>&locationdata.world=<world>&c:resolve=...&c:limit=1`
- Guild: `guild/?name=<name>&world=<world>&c:resolve=members(...)&c:limit=1`

---

## Character sheet — stat highlight feature

When the user hovers a stat label in the left panel, item slots on the paperdoll are highlighted:
- **Bright green** (`rgba(34,255,34,0.13)`) — the item itself has that stat
- **Dim green** (`rgba(34,255,34,0.05)`) — only an adorn on the item has it

Implementation:
- On character load, `CharacterView` calls `prefetchItem()` for every equipped item and adorn, populating `ItemTooltip._cache`
- `getHighlight(item)` reads from the cache and calls `statMatches(panelLabel, statDisplayName)`
- `statMatches` does case-insensitive substring matching + a `STAT_ALIASES` table for known divergences (e.g. `'crit chance' → 'critical chance'`, `'physical mit' → 'mitigation'`, `'elemental mit' → 'resistances'`)
- `mitigation` is a top-level property on `ItemDetail` (not in `stats[]`), so Physical Mit and Armor get a special `d.mitigation > 0` check

---

## Item tooltip — key details

- Colours from `image/tooltip.py`: `BG=#0a0a0e`, `BORDER_OUTER=#c49e2c`, `BORDER_INNER=#364c5c`, `C_PRIMARY=#22ff22`, `C_SECONDARY=#3cc0c0`, `C_GOLD=#e6e970`
- Quality glow: Fabled=`#ff939d`/glow`#df535f`, Legendary=`#ffc993`/glow`#D56900`, Treasured/Mastercrafted=`#93d9ff`/glow`#D56900`
- Compound quality strings (e.g. "Mastercrafted Fabled") — last recognised word wins for colour
- Food/drink: single "Effects:" header rather than per-effect name headers (`isConsumable` flag)
- Zero stats filtered: `s.value !== 0`, mitigation only shown if `> 0`
- Viewport clamping: `useLayoutEffect` + `useRef` measures rendered height after content loads

---

## Character claiming — data model

```
users (discord_id PK, discord_name, avatar, first_seen, last_seen)
character_claims (id PK, discord_id FK, character_name, status, requested_at, reviewed_at, reviewed_by, note)
```

Claim statuses: `pending` → `approved` / `rejected` / `withdrawn` (user cancelled) / `superseded` (replaced by a new approved claim).

Rules enforced by `web/db.py`:
- `submit_claim` auto-withdraws any existing pending claim before inserting a new one
- `review_claim` supersedes any previously-approved claim when approving a new one (one approved claim per user at most)
- `get_active_claim` returns approved first, then pending — ignores withdrawn/rejected/superseded

---

## Adornment display

On the paperdoll, each item's adorn slots are shown as small coloured chips below the item name.

- Census key for adorns on equipped items: `adornment_list` (array of `{color, id?}` — `id` present means equipped)
- If `id` present: look up item name from the local item DB via `find_by_id`
- Colour map: White/Yellow/Red/Green/Blue/Purple/Orange/Turquoise/Black → specific hex values

**Name shortening**: `<Adj> Adornment of <Name> (Quality)` → `Adj <Name> (X)` where X is a tier letter (F/L/T/U/C) in the appropriate quality colour. Uses last-word matching for compound quality strings.

---

## Tooltip event handling (paperdoll)

Adorn chips use `data-adorn-id` attributes. The parent `SlotRow` uses `onMouseOver` (not `onMouseEnter`) with delegated detection:

```tsx
onMouseOver={e => {
  const adornEl = (e.target as HTMLElement).closest('[data-adorn-id]')
  if (adornEl) { onShow(adornEl.getAttribute('data-adorn-id')!, e); return }
  onShow(item.item_id!, e)
}}
```

This ensures moving from an adorn chip back to the item area always re-triggers the item tooltip (mouseenter would not re-fire since the mouse never left the parent).

---

## AA tree notes

### Data files (`data/AAs/`)
- `trees/{id}.json` — one file per tree, contains `alternateadvancement_list[0]` with `name`, `ofyclassification`, `alternateadvancementnode_list`
- `bg_sprite.png` — sprite sheet: 7 backdrop circles (44px) then 3 badge circles (24px: white/yellow/green)
  - Backdrop x-offsets: `{-1:0, 456:45, 457:90, 458:135, 459:180, 460:225, 461:270}`
  - Badge x-offsets: yellow (not maxed) = 340, green (maxed) = 365

### Tree type detection
`detect_tree_type` checks xcoord sets, max ycoord, `ofyclassification`, node `classification`. Returns: `class`, `subclass`, `shadows`, `heroic`, `tradeskill`, or one of several unimplemented types that fall back to `render_subclass_tree`.

### Coordinate systems (native 640×480, SCALE=2)
- **class**: x at columns `{1:86, 4:206, 7:327, 10:447, 13:567}`, y = `42 + ycoord × 66.67`
- **subclass**: anchor x=234 at xcoord 15, step 155/12 px/unit; y = `42 + ycoord × 21.05`
- **shadows**: native 632×472; x = `40 + xcoord × 13` scaled; y from lookup `{1:59, 6:166, 11:273, 16:377}` scaled
- **heroic**: x = `65 + (xcoord-2) × 13`, y = `50 + (ycoord-1) × 22`
- **tradeskill**: x = `65 + (xcoord-2) × 13`, y = `60 + (ycoord-1) × 21`

---

## Bot cog notes

### Guild (`/guild`)
- Members without a `type` dict are filtered (incomplete data)
- Rank resolved from `rank_list` in guild response; sorted by rank ID asc, level desc
- Sent as `.txt` attachment if table > 2000 chars

### Spellcheck (`/spellcheck`)
- Filters: `level > 0`, type = `spells` or `arts`, `given_by` not `alternateadvancement` or `class`
- Deduplicates by stripping trailing Roman numerals (I–XX), keeping highest-level per base name per type

### `/aacheck`
- Five static tree choices; fetches character AAs at runtime, matches by `detect_tree_type`
- Badge: yellow if `tier < maxtier`, green if `tier >= maxtier`; positioned bottom-right of node
- Caption shows real tree name + total points spent

---

## Running locally

```bash
make dev            # backend (port 8000) + frontend dev server (port 5173)
make bot            # Discord bot only
make build          # build React frontend into frontend/dist/
```

Or manually:
```bash
python -m uvicorn web.app:app --reload --port 8000
cd frontend && npm run dev
python main.py      # Discord bot
```

## Deployment

- Railway, Nixpacks, `python main.py` start command (bot only) or configure for uvicorn (web)
- Push to `main` triggers redeploy
- New slash commands take up to 1h globally; instant in registered guild IDs
- Do not push until local testing passes
