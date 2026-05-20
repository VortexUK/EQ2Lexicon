# EQ2CensusBot

A Discord bot **and** web companion site for EverQuest 2 (TLE server). Queries the [Daybreak Census API](https://census.daybreakgames.com) to provide item tooltips, guild summaries, character sheets, and more.

---

## Discord Bot Commands

| Command | Description |
|---|---|
| `/item <name\|id\|game link>` | Renders an EQ2 item tooltip as an image |
| `/guild <name>` | Tabular member summary for a guild on the configured world |
| `/spellcheck <name>` | Summarises a character's spell/art tiers |
| `/spellcheck <name> details:True` | Full spell list ordered by tier then level |
| `/aacheck <character> <tree>` | Renders a character's AA allocations for a chosen tree |

**`/aacheck` tree options:** Class · Subclass · Shadows · Heroic · Trade

---

## Web Companion Site

A React + FastAPI site served at `http://localhost:8000` (dev: `http://localhost:5173`).

### Features

- **Character sheet** — full stat panel, paperdoll with tier-coloured item names, adornment chips, item and adorn tooltips on hover, stat-to-item highlight (hover a stat on the left to see which items contribute it)
- **Item tooltips** — rendered in HTML matching the in-game style, with quality glow colours, stats, effects, adornment slots, and flags
- **Discord login** — OAuth2 sign-in via Discord
- **Character claiming** — users link their Discord account to their EQ2 character; claims require admin approval before taking effect
- **Admin panel** — approve or reject pending claims with an optional rejection note; view full claim history

---

## Project Structure

```
main.py                  # Discord bot entry point

bot/
  bot.py                 # EQ2Bot — registers cogs, syncs slash commands
  cogs/
    items.py             # /item command
    guild.py             # /guild command
    spellcheck.py        # /spellcheck command
    aacheck.py           # /aacheck command

census/
  client.py              # CensusClient — all Census API HTTP calls
  models.py              # Dataclasses: ItemData, EquipmentSlot, AdornSlot, CharacterOverview, …
  constants.py           # STAT_MAP, class groups, ARCHETYPES, CLASS_GROUPS
  db.py                  # Item catalogue SQLite DB (data/items/items.db)

image/
  tooltip.py             # PIL item tooltip renderer (2× supersampling)
  aa_tree.py             # AA tree renderers

web/
  app.py                 # FastAPI application factory
  db.py                  # Users / character_claims SQLite DB (data/users.db)
  routes/
    health.py            # GET /api/health
    auth.py              # Discord OAuth2 — login, callback, /me, logout
    character.py         # GET /api/character/{name}
    item.py              # GET /api/item/{item_id}
    claim.py             # GET|POST|DELETE /api/claim  (character claiming)
    admin.py             # GET /api/admin/claims  +  approve/reject endpoints

frontend/
  src/
    App.tsx              # React Router routes
    pages/
      HomePage.tsx       # Search + login + claim status strip
      CharacterPage.tsx  # Full character sheet with stat panel, paperdoll, tooltips
      ClaimPage.tsx      # Claim submission / status / change character
      AdminPage.tsx      # Admin claim queue + history
    hooks/
      useAuth.ts         # Discord auth state hook
      useClaim.ts        # Character claim state hook
    components/
      ItemTooltip.tsx    # HTML item tooltip (portal, viewport-clamped)

data/
  items/
    items.db             # Local item catalogue (SQLite, downloaded from Census)
    icons/               # Item icon PNGs
  users.db               # Users + character claims (created automatically)
  AAs/                   # AA tree JSON files and node icons

scripts/                 # Local preview and download scripts (see below)
```

---

## Setup

### Prerequisites

- Python 3.11+
- Node.js 18+ (for the web frontend)

### 1. Install dependencies

```bash
pip install -r requirements.txt
cd frontend && npm install
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your values
```

Key variables:

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token from the Discord developer portal |
| `CENSUS_SERVICE_ID` | Census API service ID — register at census.daybreakgames.com for a higher rate limit (default `example` is rate-limited) |
| `EQ2_WORLD` | EQ2 server name (default `Varsoon`) |
| `DISCORD_CLIENT_ID` | OAuth2 client ID for the web login |
| `DISCORD_CLIENT_SECRET` | OAuth2 client secret |
| `DISCORD_REDIRECT_URI` | OAuth2 callback URL (default `http://localhost:8000/api/auth/callback`) |
| `SESSION_SECRET` | Random secret for signing session cookies — generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_DISCORD_IDS` | Comma-separated Discord user IDs who can approve character claims |
| `SERVER_MAX_LEVEL` | Optional — cap item lookups to a TLE expansion level (e.g. `70`) |

### 3. Running locally

**Quickest — use Make:**

```bash
make dev        # starts both backend and frontend dev servers
```

Or start each manually in separate terminals:

```bash
# Terminal 1 — backend
python -m uvicorn web.app:app --reload --port 8000

# Terminal 2 — frontend dev server
cd frontend && npm run dev
```

Then open **http://localhost:5173** (Vite dev server, proxies API to port 8000).

To run just the Discord bot:

```bash
python main.py
```

### 4. Building for production

```bash
make build      # builds the React frontend into frontend/dist/
```

The FastAPI app serves the built frontend automatically when `frontend/dist/` exists.

---

## Deployment (Railway)

The repo includes a `railway.toml` configured for Nixpacks.  
Set all required environment variables in the Railway dashboard, then push to `main` to trigger a redeploy.

---

## Preview / Utility Scripts

```bash
# Item inspection
python scripts/preview_item.py "Faded Black Hood"   # render tooltip image
python scripts/inspect_item.py "Faded Black Hood"   # dump raw Census JSON

# Guild & characters
python scripts/preview_guild.py "Exordium"
python scripts/preview_spellcheck.py Sihtric
python scripts/preview_spellcheck.py Sihtric --details
python scripts/preview_aa_tree.py 25               # render AA tree by ID
python scripts/preview_aacheck.py Menludiir        # list character AA trees
python scripts/preview_aacheck.py Menludiir Templar # render a specific tree

# Data downloads
python scripts/download_aa_trees.py               # fetch all AA tree JSONs from Census
python scripts/download_aa_icons.py               # fetch all AA node icon PNGs
```

---

## Census API Notes

- Base URL: `https://census.daybreakgames.com`
- Item lookup supports display name, numeric ID, or in-game link (`\aITEM 12345 ...`)
- Game link IDs are signed 32-bit integers; the client converts negative values to unsigned automatically
- The `example` service ID is rate-limited — register at the Census site for production use
