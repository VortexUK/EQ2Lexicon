# EQ2 Lexicon

[![CI](https://github.com/VortexUK/EQ2Lexicon/actions/workflows/ci.yml/badge.svg)](https://github.com/VortexUK/EQ2Lexicon/actions/workflows/ci.yml)
[![CodeQL](https://github.com/VortexUK/EQ2Lexicon/actions/workflows/codeql.yml/badge.svg)](https://github.com/VortexUK/EQ2Lexicon/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A web companion site for EverQuest 2 (TLE), with a Discord bot for spot checks. Queries the [Daybreak Census API](https://census.daybreakgames.com) and the [ACT plugin](https://github.com/VortexUK/EQ2LexiconACTPlugin) to bring character data, guild parses, and raid information into one place.

Live at <https://eq2lexicon.com> · plugin API at <https://parses.eq2lexicon.com>.

---

## Web Companion Site

A React + TypeScript (Tailwind v4) + FastAPI site. Each EQ2 server gets its own subdomain (e.g. `varsoon.eq2lexicon.com`); one Discord login covers all.

### Features

- **Character sheet** — full stat panel, paperdoll with tier-coloured item names, adornment chips, item and adorn tooltips on hover, stat-to-item highlight
- **Spells tab** — deduplicated spell/art list; tier pip icons (Apprentice → Grandmaster); "Raid Ready" and "Fully Mastered" progress bars; spell blocklist support
- **AAs tab** — visual AA tree with tier badges (Class / Subclass / Shadows / Heroic / Trade); per-tree point totals
- **Item tooltips** — HTML-rendered in-game style, with quality glow colours, stats, effects, adornment slots, and flags
- **Item search** — browse and filter the full item catalogue by name, level, rarity, and slot
- **Recipes** — searchable recipe catalogue (~70 k recipes); shopping-list panel with quantity tracking
- **Parses & rankings** — encounter DPS/HPS boards ingested from the ACT plugin; per-character and per-encounter breakdowns; raid-zone rankings
- **Raid strategies** — per-encounter strategy notes (wiki-seeded, admin/contributor-editable with revision history)
- **Item watch** — track specific items for guild members; officer review workflow
- **Character claiming** — link a Discord account to an EQ2 character (admin-approved)
- **Multi-server** — single deployment serves multiple TLE servers via subdomains; admin-editable per-server settings

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

## Project Structure

```
bot/            Discord bot cogs (/item, /guild, /spellcheck, /aacheck)
census/         Census API client, dataclasses, SQLite catalogues (items, spells, recipes, zones, raids)
image/          PIL renderers — item tooltips and AA trees
web/            FastAPI app, routes, cache, auth, SSE refresh
frontend/       React/TypeScript UI (Tailwind v4, src/components/ui/, src/hooks/, src/lib/)
data/           Local SQLite DBs, AA tree JSON + icons, spell icons (gitignored / Railway volume)
scripts/        Preview, download, and build utilities — see scripts/README.md
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

**Activate the pre-push hook** (recommended) — runs the same lint, type, and test checks as CI before every `git push`:

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

See [scripts/README.md](scripts/README.md) for the full list of preview, download, and DB-build scripts.

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

## Logging

The app reads two env vars at startup (`configure_logging()` in `web/lib/logging_config.py`):

- `LOG_LEVEL` — `DEBUG` / `INFO` (default) / `WARNING` / `ERROR`. Setting `DEBUG` is safe for one-off debug sessions but noisy on cache + Census layers.
- `LOG_FORMAT` — `text` (default, human-readable) or `json` (one structured record per line — recommended for Railway, where the aggregator parses JSON natively).

Every log record carries `request_id`, `user_id`, and `world` fields populated by `RequestContextMiddleware`, so a single user-reported issue can be correlated across log lines.

Audit-trail events (claim approvals, role grants, parse purges, etc.) emit via the `eq2.audit` logger — filter on logger name in your aggregator to build an audit dashboard.

---

## Contributing & Security

- Dev setup, the test gates, and the PR checklist live in [CONTRIBUTING.md](CONTRIBUTING.md).
- Architecture, key files, and environment variables are documented in [CLAUDE.md](CLAUDE.md).
- Found a security issue? See [SECURITY.md](SECURITY.md) — please report privately, not via public issues.

## License

[MIT](LICENSE).
