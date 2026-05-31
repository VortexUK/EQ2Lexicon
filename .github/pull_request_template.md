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
If this PR implements a brainstormed plan, link the spec + plan:
  - Spec:  docs/superpowers/specs/YYYY-MM-DD-...md
  - Plan:  docs/superpowers/plans/YYYY-MM-DD-...md
Otherwise link any related GitHub issue.
-->

## Pre-push gate

- [ ] `ruff format --check`, `ruff check`, `pyright`, `pytest` all green locally
- [ ] `cd frontend; npm run typecheck && npm run build && npm test` all green locally
- [ ] New tests cover the new behaviour (or change is doc/refactor only)

## Project-specific checks

<!-- Tick the ones that apply; delete the rest. -->

- [ ] **Schema change** — migration added to `parses/db.py:_MIGRATIONS` (or the relevant module) and is idempotent on a pre-migration DB shape
- [ ] **New env var** — also added to `.env.example` and documented in `CLAUDE.md`
- [ ] **Backend route uses `run_sync`** — any call to `current_world()` / `current_server()` is captured *outside* the threadpool closure (see the 2026-05-31 hotfix; `run_sync` now propagates contextvars, but explicit capture is still the convention)
- [ ] **Census-dependent code path** — covered by an in-memory fixture or mocked; no live Census calls in tests
- [ ] **DB artifact** (`.db` file) — NOT committed; built locally and uploaded to the Railway volume separately
- [ ] **Frontend visual change** — built and eyeballed locally; screenshots in this PR body if non-trivial
- [ ] **New ui/ primitive or hook** — exported from the barrel (`frontend/src/components/ui/index.ts` etc.)
- [ ] **Per-server-aware feature** — works on Varsoon AND Wuoshi subdomains (or the active per-server context propagates correctly)

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
