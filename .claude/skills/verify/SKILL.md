---
name: verify
description: Run the right lint, type and test checks for what changed and see only the failures. Use before saying work is done, before a commit, and after fixing a failing check.
---

# Verify

One command picks the checks that match the change and prints failures only, so a
green run costs a few lines instead of a full test log.

```bash
uv run --frozen python scripts/tools/verify.py            # scoped to changed and untracked files
uv run --frozen python scripts/tools/verify.py --all      # full pre-push gate, plus eslint and vite build
uv run --frozen python scripts/tools/verify.py --py       # backend side only (--fe for frontend)
uv run --frozen python scripts/tools/verify.py --base origin/main   # also count commits ahead of a ref
uv run --frozen python scripts/tools/verify.py --dry-run  # show the planned stages, run nothing
```

Output is one line per stage: `PASS`, `SKIP <reason>` or `FAIL` followed by that
stage's output, trimmed. Fix what the FAIL blocks show and rerun. `--files a.py b.tsx`
checks a named set instead of the git change set.

The last line is the verdict: `verify: OK` (exit 0), `verify: FAIL` (exit 1), or
`verify: INCOMPLETE` (exit 2) when a needed tool is missing, for example no
`frontend/node_modules`. INCOMPLETE means part of the change was not checked.

## What the default (changed) mode runs

- Python: `ruff format --check` and `ruff check` on the changed files, `pyright`, then
  pytest on the tests mapped from the change. A changed backend module `foo.py` maps to
  every `test_foo*.py` under `tests/`; SQL sidecars, migrations, `backend/server/db/` and
  the agent docs each pull in their guard test.
- Frontend (only when something under `frontend/` changed): `tsc -b`, `vitest related`
  on the changed files, `eslint` on the changed files.

A `no mapped tests: <file>` line means a changed backend file selected no test. That is
not a pass: find the test that covers it (`codemap` helps), run it by path, or write one.

## When to run `--all`

- Before a push. It is what the pre-push hook and CI run.
- After touching shared infrastructure: `backend/pg.py`, `backend/db_catalogue.py`,
  `tests/conftest.py`, `tests/fixtures/`, `frontend/src/hooks/`, `frontend/src/components/ui/`.
- When changed mode printed `no mapped tests` and you could not find a narrower test.

## Things that look like failures and are not, and the reverse

- pytest needs the local PostgreSQL 17 test database. A second pytest run started at the
  same time waits on an advisory lock; it has not hung.
- A full run sometimes ends `N passed, 1 error` with a RuntimeError at teardown on a
  random test. Rerun once. Treat it as real only if the same test errors again or
  anything is FAILED.
- Frontend typecheck is `tsc -b`. `tsc --noEmit -p tsconfig.json` checks nothing here,
  because the root tsconfig only references sub-projects.

Report what ran and what it showed. A skipped stage is "not run", never "passed".
