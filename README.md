# EQ2 Lexicon

[![CI](https://github.com/VortexUK/EQ2Lexicon/actions/workflows/ci.yml/badge.svg)](https://github.com/VortexUK/EQ2Lexicon/actions/workflows/ci.yml)
[![CodeQL](https://github.com/VortexUK/EQ2Lexicon/actions/workflows/codeql.yml/badge.svg)](https://github.com/VortexUK/EQ2Lexicon/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Discord bot **and** web companion site for EverQuest 2 (TLE server). Queries the [Daybreak Census API](https://census.daybreakgames.com) to provide item tooltips, guild summaries, character sheets, parses ingested from the [ACT plugin](https://github.com/VortexUK/EQ2LexiconACTPlugin), and more.

Live at <https://eq2lexicon.up.railway.app>.

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
- **Spells tab** — full deduplicated spell/art list in a two-column layout; each spell shows its icon (backdrop + foreground layered) and a row of 6 tier pip icons lit to its current tier (Apprentice → Journeyman → Adept → Expert → Master → Grandmaster); sidebar shows spells in lowest→highest tier order with progress bars for "Raid Ready" (≥ Expert) and "Fully Mastered" (≥ Master); spell blocklist support hides spells that cannot be upgraded
- **AAs tab** — AA profile selector (Class / Subclass / Shadows / Heroic / Trade); renders the visual AA tree with tier badges and shows per-tree point totals
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
  item_parser.py         # Item data parsing helpers (extracted from client.py)
  spells_db.py           # Local SQLite spell catalogue helpers

image/
  tooltip.py             # PIL item tooltip renderer (2× supersampling)
  aa_tree.py             # AA tree renderers

web/
  app.py                 # FastAPI application factory
  config.py              # Centralised env config (SERVICE_ID, WORLD)
  db.py                  # Users / character_claims SQLite DB (data/users.db)
  routes/
    health.py            # GET /api/health
    auth.py              # Discord OAuth2 — login, callback, /me, logout
    character.py         # GET /api/character/{name}
    item.py              # GET /api/item/{item_id}
    claim.py             # GET|POST|DELETE /api/claim  (character claiming)
    admin.py             # GET /api/admin/claims  +  approve/reject endpoints
    aa.py                # GET /api/character/{name}/aas
    characters.py        # GET /api/characters/search
    guild.py             # Guild endpoints (spellcheck, adorn check, roster)
    guild_officer.py     # Officer claim-review endpoints
    item_watch.py        # Item watch endpoints

frontend/
  src/
    App.tsx              # React Router routes
    pages/
      HomePage.tsx       # Search + login + claim status strip
      CharacterPage.tsx  # Full character sheet with stat panel, paperdoll, tooltips
      CharacterAAsTab.tsx  # AA tree tab
      CharacterSpellsTab.tsx  # Spells tab
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
  spells/
    spells.db            # Local spell catalogue (SQLite, seeded by download_spells.py)
    blocklist.json       # Spell names to hide from spell tab and /spellcheck
    icons/               # Spell icon PNGs (0–1177.png)

scripts/                 # Local preview and download scripts (see below)
```

---

## Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages Python + Python deps — install with `pip install uv` or the standalone installer at https://astral.sh/uv)
- Node.js 18+ (for the web frontend)

### 1. Install dependencies

```bash
uv sync --all-groups      # creates .venv, installs prod + dev deps from uv.lock
cd frontend && npm install
```

uv reads `.python-version` (3.13) and will download a matching CPython automatically if you don't have one.

Run any Python tool with `uv run <tool>` — uv resolves it inside the project venv without needing to activate it manually:

```bash
uv run pytest
uv run ruff check .
uv run python main.py
```

**Activate the pre-push hook** (recommended) — runs the same lint, type, and test checks as CI before every `git push`, so you find out about a regression in seconds instead of after CI runs:

```bash
git config core.hooksPath .githooks
```

**Adding or removing a dependency:**

```bash
uv add httpx              # adds to [project.dependencies] and updates uv.lock
uv add --group dev mypy   # adds to [dependency-groups.dev]
uv remove slowapi
```

Commit both `pyproject.toml` and `uv.lock` so deploys reproduce the exact resolved versions.

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
python scripts/download_spell_icons.py            # download all spell icon PNGs
python scripts/download_spell_icons.py --start N  # resume from icon N
python scripts/preview_spellcheck.py Sihtric --debug  # show each counted spell
```

---

## Census API Notes

- Base URL: `https://census.daybreakgames.com`
- Item lookup supports display name, numeric ID, or in-game link (`\aITEM 12345 ...`)
- Game link IDs are signed 32-bit integers; the client converts negative values to unsigned automatically
- The `example` service ID is rate-limited — register at the Census site for production use

---

## Spell Blocklist

To hide a spell from the character Spells tab and the `/spellcheck` Discord command, add its base name (without Roman-numeral rank) to `data/spells/blocklist.json`:

```json
{ "blocked": ["Fighting Chance", "Some Other Spell"] }
```

The file is re-read on every request — no restart required.

---

## Contributing & Security

- Dev setup, the test gates, and the PR checklist live in [CONTRIBUTING.md](CONTRIBUTING.md).
- Architecture, key files, and environment variables are documented in [CLAUDE.md](CLAUDE.md).
- Found a security issue? See [SECURITY.md](SECURITY.md) — please report privately, not via public issues.

## License

[MIT](LICENSE).
