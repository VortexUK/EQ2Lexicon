---
name: codemap
description: Locate code without grepping. Lists HTTP routes, stores and catalogues, SQL sidecar blocks, frontend pages, background loops and database tables as file:line. Use first when asked where something lives, or before reading files to find an endpoint, query, table or page.
---

# Codemap

A static index of the repo. One call returns `file:line` for the thing you want, which
replaces a grep followed by reading several candidate files.

```bash
uv run --frozen python scripts/tools/codemap.py routes [filter]   # METHOD /api/path  file:line  handler
uv run --frozen python scripts/tools/codemap.py stores [filter]   # PgStoreBase / PgCatalogue classes, schema, methods
uv run --frozen python scripts/tools/codemap.py sql [filter]      # "-- :name" blocks in the .sql sidecars
uv run --frozen python scripts/tools/codemap.py pages [filter]    # /route -> frontend page file (lazy or eager)
uv run --frozen python scripts/tools/codemap.py loops             # background tasks started from the app lifespan
uv run --frozen python scripts/tools/codemap.py tables [filter]   # schema.table: columns, from the local test database
```

The filter is a case-insensitive substring. Always pass one: `routes recruit`, not `routes`.

## Which one to reach for

| Question | Command |
|---|---|
| Which handler serves `/api/guild/{name}/settings`? | `routes settings` |
| Where is the query that lists recruiting guilds? | `sql recruit`, then read that block |
| What does the fights table look like? | `tables parses.fights` |
| Which file renders `/recruiting`? | `pages recruit` |
| What runs in the background, and on which schedule? | `loops`, then read the named function |
| Which store owns raid schedules? | `stores raid` |

## Limits

- It parses source and never imports the app, so anything assembled dynamically at
  runtime will not appear. If a route you expect is missing, check `_ROUTERS` in
  `backend/server/app.py`.
- `tables` reads the local test database (`TEST_DATABASE_URL`), which the pytest suite
  rebuilds from `db/migrations/`. If it is unreachable the command says so; read the
  migrations for that family instead. It never connects to production.
- For symbol-level navigation (definitions, references) use the LSP tool when a language
  server is running; codemap answers "which file", not "who calls this".
