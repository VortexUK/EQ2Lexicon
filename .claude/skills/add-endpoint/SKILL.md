---
name: add-endpoint
description: Checklist for adding a backend API route, with its store, SQL sidecar, facade registration and tests. Use when creating a new /api endpoint or a new users-schema store, so none of the fixed registration points is missed.
---

# Add an API endpoint

A new route touches a fixed set of places. Missing one shows up late: a 404, a store the
test fixtures cannot re-point, or a guard test failing in the pre-push run. Look at an
existing small domain first; `favorites` is a good model
(`backend/server/api/favorites.py`, `backend/server/db/favorites.py`,
`backend/server/db/favorites.sql`, `tests/server/test_favorites.py`).

## Route only (no new table)

1. **Router module**: `backend/server/api/<name>.py` with `router = APIRouter(tags=["<name>"])`.
   Large areas are packages (`api/parses/`, `api/character/`, `api/act/`); add to the
   package that already owns the feature rather than creating a sibling.
2. **Register it in `backend/server/app.py`**, two edits:
   - the alphabetised import block: `from backend.server.api.<name> import router as <name>_router`
   - the `_ROUTERS` list (every entry is included with `prefix="/api"`).
3. **World scoping**: take the world from `current_world()` (`backend/server/server_context.py`),
   never from the `WORLD` constant. If the handler hands work to the thread pool, read
   `current_world()` before the closure and pass the value in.
4. **Auth and limits**: use the existing dependencies in `backend/server/auth_deps.py` and the
   shared limiter in `backend/server/limiter.py`. Officer and leader checks live in
   `backend/server/api/guild.py`; reuse them (see `.claude/rules/guild.md`).
5. **Census**: a read path serves from the census store or cache and never awaits Census.
6. **Privileged writes** are audited with `audit_log("snake_case_action", actor=..., **fields)`.

## With a new store

7. **DDL**: a new migration. Use the `add-migration` skill.
8. **Store**: `backend/server/db/<name>.py` with `class XStore(PgStoreBase)`, a module-level
   `store = XStore()`, async methods using `self._db()` for writes and `self._read()` for
   single-statement reads. SQL goes in the sidecar `backend/server/db/<name>.sql`
   (`-- :name` blocks, `%s` params, unqualified table names), loaded with
   `_SQL = load_sql(__file__)`.
9. **Facade**: in `backend/server/db/__init__.py` import the store and add it to `ALL_STORES`.
   That tuple is required: the test fixtures iterate it to re-point every store at a
   scratch schema. Add bound-method aliases only if callers use the facade for this domain.
10. **Facade guard**: `tests/server/test_db_facade.py` fails unless each public store method
    is aliased on the facade or listed in `_FACADE_EXEMPT`. Pick one deliberately.
11. **Erasure**: if the table holds anything tied to a user, extend
    `backend/server/db/erasure.py` and keep the privacy page text in step.

## Tests

- `tests/server/test_<name>.py`. Store tests take the `users_schema` fixture and seed with
  `pg_conn(users_schema)`; route tests use the `app` fixture with `httpx.ASGITransport`.
- Cover the permission boundary (anonymous, wrong user, wrong world) as well as the happy path.
- No live Census in tests: use the in-memory fixtures or a mock.

## Finish

Run the `verify` skill. If the endpoint feeds a page, the frontend half is the `add-page`
skill. Describe the endpoint in the module docstring; add a line to a `.claude/rules/`
file only for an invariant that spans files.
