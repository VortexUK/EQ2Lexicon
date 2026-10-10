---
name: ops
description: Operator runbook index for EQ2Lexicon - catalogue refresh, zone and raid-strategy seeding, backup restore drill, deploy and environment. Invoked by the user.
disable-model-invocation: true
---

# Operations runbooks

Every script here writes to whichever database `DATABASE_URL` resolves to
(`DATABASE_URL`, then `SUPABASE_DB_URL`, then `POSTGRES_CONNECTION_STRING`). With the
production DSN in `.env`, a script run is a production write. Before running anything,
state which database it will hit and get a yes.

| Task | Runbook | Notes |
|---|---|---|
| Refresh items, spells, recipes, AAs from Census | `docs/runbooks/catalogue-refresh.md` | Order matters: items, spells, recipes, `build_recipe_classes`, `backfill_recipe_levels --rebuild`. Downloads are resumable. |
| Rebuild zone metadata | `docs/runbooks/zones-and-raids-seed.md` | Boss rosters are curator data and survive a rebuild, but a removed or renamed zone is pruned with its roster. Read the prune count the script prints. |
| Seed or re-scrape raid strategies | `docs/runbooks/zones-and-raids-seed.md` | Manual edits (`SOURCE_MANUAL`) are never overwritten. |
| Agent-assisted strategy rewrite | `docs/runbooks/raid-strategy-agent-polish.md` | Export, rewrite in parallel, apply; three passes. |
| Restore a backup, run the drill | `docs/runbooks/backup-restore.md` | Create `pg_trgm` in the target first. Nightly dumps are in R2, 30 days kept. |
| Deploy, environment variables, pooler sizing | `docs/runbooks/deploy.md` | A deploy is a migration run; deploys overlap. |
| Every script and what it does | `scripts/README.md` | |

Read the runbook for the task in full before running its first command; they are short.

## Standing cautions

- Long downloads: run in the background and report progress, do not block on them.
- After a catalogue refresh, static-table counts are the check. Compare before and after.
- Commands handed to the user to run themselves are written for PowerShell.
- Nothing here is committed data: catalogue rows live in the database, never in the repo.
