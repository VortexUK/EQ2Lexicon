# Contributing

Thanks for considering a contribution. This is a small project; the process is light.

## Before you start

For non-trivial changes, [open an issue](https://github.com/VortexUK/EQ2Lexicon/issues/new/choose) first to discuss the approach. Small fixes (typos, docs, obvious bugs) can go straight to a PR.

For security issues, see [SECURITY.md](SECURITY.md) — don't file public issues for vulnerabilities.

## Dev setup

This is a Python ([uv](https://docs.astral.sh/uv/)) backend + Discord bot, with a React/TypeScript (Vite) frontend.

```powershell
# Backend — from the repo root
uv sync --all-groups           # provisions .venv from uv.lock
uv run uvicorn web.app:app --port 8000 --reload

# Frontend — from frontend/
npm ci
npm run dev
```

Convenience launchers live in `scripts/` (`dev_backend.ps1`, `dev_frontend.ps1`, `start.ps1`).

Copy `.env.example` to `.env` and fill in the values (Discord token, Census service ID, etc.). See [CLAUDE.md](CLAUDE.md) for the full environment-variable table.

Activate the pre-push hook once after cloning:

```powershell
git config core.hooksPath .githooks
```

It runs the same gates CI runs (see below).

## The gates (pre-push + CI)

| Gate | Command |
|------|---------|
| Python format | `uv run ruff format --check .` |
| Python lint | `uv run ruff check .` |
| Python types | `uv run pyright` |
| Python tests | `uv run pytest` |
| Frontend types | `npx tsc -b` (in `frontend/`) |
| Frontend tests | `npm test` (in `frontend/`) |
| Frontend build | `npm run build` (in `frontend/`) |

To auto-fix Python formatting: `uv run ruff format .`

## Code conventions

- `pyproject.toml` (`[tool.ruff]`) is the source of truth for Python style — 120-col lines, `py313` target.
- Web routes import config from `backend/server/config.py`, never `os.getenv` directly.
- New SQLite layers follow the `backend/eq2db/recipes.py` pattern (`_CREATE_*` SQL constants, `_MIGRATIONS` list, `init_db()` with WAL).
- **Frontend styling is Tailwind v4 (enforced).** Utility classes for all static styling; `style={{…}}` only for runtime-computed values. Use the `components/ui` primitives (`Button`/`Card`/`SectionLabel`), the theme utilities (`text-gold`, `bg-surface`, `text-rarity-*`, …), and `rarityColors.ts` for tier/rarity colours. Don't add static inline styles, per-page style-object consts, or new `TIER_COLOUR` maps. Full rules in [CLAUDE.md → Frontend styling](CLAUDE.md).
- Frontend components keep shared bits in their parent page module; see the `CharacterPage.tsx` / tabs split in [CLAUDE.md](CLAUDE.md).
- Comments explain *why*, not *what*.

## PR checklist

- [ ] Pre-push hook passes locally (all gates above)
- [ ] New behaviour has tests (`tests/` for Python, `*.test.tsx` for frontend)
- [ ] User-facing or architectural changes are reflected in [CLAUDE.md](CLAUDE.md) / [README.md](README.md)
- [ ] No secrets, tokens, or real Discord IDs committed
- [ ] Commit messages explain the *why*

## Deployment

Push to `main` triggers a Railway redeploy. Don't push until local gates pass.
