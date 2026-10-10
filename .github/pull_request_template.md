<!--
Pull request template for EQ2Lexicon.

The headings below are prompts, not required sections. Delete the ones
that don't apply. Most reminders match the project conventions in
CLAUDE.md (Codebase notes) and the persisted memory file.
-->

## Summary

<!-- One or two sentences: what does this PR do, and why? -->

## Linked plan / spec / issue

<!--
Link the related GitHub issue, and the ADR under docs/decisions/ if this PR implements one.
-->

## Pre-push gate

- [ ] `ruff format --check`, `ruff check`, `pyright`, `pytest` all green locally
- [ ] `cd frontend; npm run typecheck && npm run build && npm test` all green locally
- [ ] New tests cover the new behaviour (or change is doc/refactor only)

## Project-specific checks

<!-- Tick the ones that apply; delete the rest. -->

- [ ] **Schema change**: a new `db/migrations/NNNN_<family>.sql` (never an edit to an applied one), idempotent and lock-short; `tests/test_pg_migrate.py` green
- [ ] **New env var**: also added to `.env.example` and `docs/runbooks/deploy.md`
- [ ] **Backend route uses `run_sync`** — any call to `current_world()` / `current_server()` is captured *outside* the threadpool closure (see the 2026-05-31 hotfix; `run_sync` now propagates contextvars, but explicit capture is still the convention)
- [ ] **Census-dependent code path** — covered by an in-memory fixture or mocked; no live Census calls in tests
- [ ] **Catalogue data**: refreshed by running the download / build scripts against the database (`docs/runbooks/catalogue-refresh.md`); no data files committed
- [ ] **Frontend visual change** — built and eyeballed locally; screenshots in this PR body if non-trivial
- [ ] **New ui/ primitive or hook** — exported from the barrel (`frontend/src/components/ui/index.ts` etc.)
- [ ] **Per-server-aware feature** — works on Varsoon AND Wuoshi subdomains (or the active per-server context propagates correctly)
- [ ] **New convention or feature notes**: written in the module docstring, a `.claude/rules/` file or a skill, not added to `CLAUDE.md`

## Deployment notes

<!--
Anything the reviewer should know before this merges to main and Railway
redeploys. Examples:
  - Requires a new env var set on Railway before deploy.
  - Triggers a lazy backfill on first read; first request post-deploy
    may be slower than steady state.
  - Schema migration runs at startup; safe to deploy without downtime.
  - Frontend bundle size grew by N kB.
-->

## Out of scope / follow-ups

<!--
Anything intentionally NOT in this PR that you've noted for later.
Helps the reviewer understand the boundary you've drawn.
-->
