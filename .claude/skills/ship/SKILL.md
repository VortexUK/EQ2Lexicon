---
name: ship
description: Commit, push and confirm the deploy for finished work in this repo. Use when the user says to ship, push or deploy, or asks for a commit to be prepared for main.
---

# Ship

A push to `main` deploys to production, and a deploy runs migrations. The steps below
keep that from being a surprise.

## Before committing

1. **Scope.** `git status` and `git diff --stat`. Decide what belongs in this commit. If
   the tree holds work that is not part of the task, leave it out; never `git add -A`
   over someone's in-progress changes.
2. **Verify.** `uv run --frozen python scripts/tools/verify.py --all`. Fix failures;
   do not push past them.
3. **Visual work waits.** Frontend or styling changes are committed only after the user
   has looked at the built result. Build it (`npm run build` in `frontend/`), say what
   changed, and wait.
4. **Docs.** New conventions go in the module docstring, a `.claude/rules/` file or a
   skill. `CLAUDE.md` does not grow.

## Commit

- Conventional subject, as in the log: `feat(guild): ...`, `fix(pg): ...`, `docs(env): ...`.
- The body says why, and names anything operational: a new migration, a new environment
  variable, a manual step.
- Stage files by name.

## Push

Ask before pushing unless the user has already said to push this work. "Push it" after
you offered a narrow and a broad option means the broad one.

The pre-push hook runs the whole gate (tsc, vitest, ruff, pyright, pytest) and takes
several minutes. Do not bypass it.

**When local `main` also carries unrelated work that fails the hook**, ship the commit
from a clean worktree instead of stashing:

1. `git worktree add -b push-tmp <scratch-dir>/push-wt origin/main`
2. In that worktree: `git cherry-pick -x <sha>`.
3. The hook needs `frontend/node_modules` and `.venv` there. Link them to the real
   checkout as directory junctions, created from PowerShell with
   `New-Item -ItemType Junction`.
4. `git push origin HEAD:main` from the worktree.
5. Back in the main checkout: `git rebase --autostash origin/main`.
6. Tear down in this order: delete each junction with PowerShell
   `(Get-Item <junction>).Delete()`, which removes only the link; then remove the worktree
   directory, `git worktree prune`, `git branch -D push-tmp`. Removing the directory first
   would follow the junctions into the real `node_modules` and `.venv`. Afterwards confirm
   both still exist in the main checkout.

## After the push

1. CI: `gh run watch` for the push, or `gh run list --limit 3`. Railway waits for CI.
2. Deploy: use the `prod-logs` skill to see the migration lines and the app start.
3. Which paths trigger a deploy is set by `watchPatterns` under `[build]` in
   `railway.toml`. Read it before telling the user a push will or will not deploy.
4. A failed deploy leaves the previous container serving. Say so, then diagnose.

Report what was pushed (hash and subject), the CI result and the deploy state. If any of
those was not checked, say that it was not.
