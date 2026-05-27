# Per-Server URLs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve `varsoon.eq2lexicon.com` and `wuoshi.eq2lexicon.com` from one deployment, resolving the active EQ2 server from the request `Host` so characters/claims/parses/leaderboards/settings scope per-server while users/roles/approvals stay universal.

**Architecture:** An ASGI middleware resolves `Host` → active server (from a DB-backed `servers` registry) and stores it in a request-scoped `contextvar`; `current_world()`/`current_server()` accessors replace the module-level `_WORLD`. Per-server data tables gain a `world` column (backfilled to `Varsoon`); per-server settings (max level, current xpac, launch date) move from env vars into the registry; one session cookie spans both subdomains via `SESSION_COOKIE_DOMAIN`.

**Tech Stack:** Python 3.13, FastAPI/Starlette, stdlib `sqlite3` + `aiosqlite`, contextvars; React 19 + TS + Vite + Tailwind v4. Tests: `pytest`/`pytest-asyncio` + httpx `ASGITransport`, `tsc`. Tooling: `uv run …` (uv on PATH), ruff, pyright. Branch: `feature/per-server-urls` (already created off `feature/census-persistence` HEAD).

Spec: `docs/superpowers/specs/2026-05-27-per-server-urls-design.md`.

---

## File Structure

**New (backend):**
- `web/server_context.py` — `Server` dataclass; in-memory registry loaded from the `servers` table; `current_server()`/`current_world()` contextvar accessors + `get_server()` FastAPI dependency; `ServerContextMiddleware` (Host → active server). One clear responsibility: "what server is this request for".
- `web/routes/server.py` — `GET /api/server` returning the active server for the current subdomain.

**Modified (backend):**
- `web/db.py` — `servers` table + `world` column on `character_claims` & `item_watch`; registry/settings + scoped-claim/scoped-watch helpers; table-rebuild migrations for changed uniqueness.
- `web/app.py` — register `ServerContextMiddleware`; `SessionMiddleware(domain=SESSION_COOKIE_DOMAIN)`; load the registry on startup; register the server router.
- `census/config.py` / `web/config.py` — add `SESSION_COOKIE_DOMAIN`; keep `WORLD` as the default-server selector.
- Per-server route modules (`_WORLD` → `current_world()`): `web/routes/character.py`, `characters.py`, `guild.py`, `guild_officer.py`, `aa.py`, `claim.py`, `item_watch.py`, `notifications.py`, `parses.py`, `rankings.py`, and `web/census_refresh.py`.
- `web/routes/rankings.py`, `web/routes/aa.py` — read `max_level`/`current_xpac` from `current_server()` instead of env.
- `parses/db.py` + `web/routes/parses.py` — `encounters.world`, `(world, act_encid)` idempotency, attribution by `logger_server`, world-scoped reads.
- `web/routes/admin.py` (+ claim/parse admin) — per-server claim/parse scoping; per-server settings editor.

**Modified (frontend):**
- `frontend/src/hooks/useServer.tsx` (new) — fetches `/api/server`, exposes the active server.
- `frontend/src/App.tsx` — header server name + "switch server" link; wrap in provider.
- launch-timer + AA-cap consumers read from `useServer()`.

**Tests:** `tests/web/test_server_context.py`, `tests/web/test_servers_db.py`, additions to `tests/web/test_claim*.py`, `test_item_watch.py`, `tests/parses/` + `tests/web/test_parses*.py`, `tests/web/test_rankings.py`, `tests/web/test_aa.py`, `tests/web/test_admin*.py`, `tests/web/test_server_route.py`.

**Conventions:** every task → `uv run ruff format <files>`, `uv run ruff check <files>`, `uv run pyright <files>`, run the named tests; commit staging ONLY the task's files (the tree may carry unrelated WIP). Frontend: `cd frontend; npm run typecheck && npm run build`. Don't push until the user asks.

---

## Phase 1 — Server registry + context foundation

### Task 1: `servers` table + DB helpers

**Files:** Modify `web/db.py`; Test `tests/web/test_servers_db.py` (new).

- [ ] **Step 1: Write failing tests** — `tests/web/test_servers_db.py`:
```python
from __future__ import annotations

from web import db


def test_servers_seeded_and_lookups(tmp_path):
    p = tmp_path / "users.db"
    db.init_db(p)
    rows = db.list_servers_sync(p)
    worlds = {r["world"] for r in rows}
    assert {"Varsoon", "Wuoshi"} <= worlds
    v = db.get_server_by_subdomain_sync("varsoon", p)
    assert v is not None and v["world"] == "Varsoon"
    w = db.get_server_by_world_sync("Wuoshi", p)
    assert w is not None and w["subdomain"] == "wuoshi"
    assert db.get_server_by_subdomain_sync("nope", p) is None


def test_upsert_server_updates_settings(tmp_path):
    p = tmp_path / "users.db"
    db.init_db(p)
    db.upsert_server_settings_sync("Wuoshi", max_level=70, current_xpac="Sentinel's Fate", launch_dt="2026-07-01T18:00:00Z", path=p)
    w = db.get_server_by_world_sync("Wuoshi", p)
    assert w["max_level"] == 70
    assert w["current_xpac"] == "Sentinel's Fate"
    assert w["launch_dt"] == "2026-07-01T18:00:00Z"
```

- [ ] **Step 2: Run — expect fail.** `uv run pytest tests/web/test_servers_db.py -q`

- [ ] **Step 3: Add the schema** — in `web/db.py`, append to the `_SCHEMA` string (before the closing `"""`):
```sql

CREATE TABLE IF NOT EXISTS servers (
    world          TEXT PRIMARY KEY,
    subdomain      TEXT NOT NULL UNIQUE,
    display_name   TEXT NOT NULL,
    max_level      INTEGER NOT NULL,
    current_xpac   TEXT,
    launch_dt      TEXT,
    updated_at     INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
```

- [ ] **Step 4: Seed on init** — in `web/db.py` `init_db`, after the existing migration block and before `conn.commit()`, add a seed that reads current env defaults for Varsoon and inserts Wuoshi if absent (idempotent — `INSERT OR IGNORE`):
```python
        # Seed the servers registry (idempotent). Varsoon takes the current env
        # values; Wuoshi starts with sensible defaults edited later in admin.
        from census import config as _cfg

        conn.execute(
            "INSERT OR IGNORE INTO servers (world, subdomain, display_name, max_level, current_xpac, launch_dt) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Varsoon", "varsoon", "Varsoon", _cfg.SERVER_MAX_LEVEL,
             os.getenv("SERVER_CURRENT_XPAC") or None, _cfg.LAUNCH_DT_ISO or None),
        )
        conn.execute(
            "INSERT OR IGNORE INTO servers (world, subdomain, display_name, max_level, current_xpac, launch_dt) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("Wuoshi", "wuoshi", "Wuoshi", _cfg.SERVER_MAX_LEVEL, os.getenv("SERVER_CURRENT_XPAC") or None, None),
        )
```
(`os` is already imported in `web/db.py`.)

- [ ] **Step 5: Add sync helpers** — append to `web/db.py` (sync helpers using stdlib `sqlite3`, mirroring the file's existing sync `init_db`; the registry loads at startup synchronously):
```python
def _server_row(row: sqlite3.Row) -> dict:
    return {
        "world": row["world"],
        "subdomain": row["subdomain"],
        "display_name": row["display_name"],
        "max_level": row["max_level"],
        "current_xpac": row["current_xpac"],
        "launch_dt": row["launch_dt"],
    }


def list_servers_sync(path: Path = DB_PATH) -> list[dict]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [_server_row(r) for r in conn.execute("SELECT * FROM servers ORDER BY display_name")]


def get_server_by_subdomain_sync(subdomain: str, path: Path = DB_PATH) -> dict | None:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM servers WHERE subdomain = ?", (subdomain.lower(),)).fetchone()
        return _server_row(row) if row else None


def get_server_by_world_sync(world: str, path: Path = DB_PATH) -> dict | None:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM servers WHERE world = ?", (world,)).fetchone()
        return _server_row(row) if row else None


def upsert_server_settings_sync(
    world: str,
    *,
    max_level: int,
    current_xpac: str | None,
    launch_dt: str | None,
    path: Path = DB_PATH,
) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE servers SET max_level = ?, current_xpac = ?, launch_dt = ?, "
            "updated_at = strftime('%s','now') WHERE world = ?",
            (max_level, current_xpac, launch_dt, world),
        )
        conn.commit()
```

- [ ] **Step 6: Run — expect pass.** `uv run pytest tests/web/test_servers_db.py -q`

- [ ] **Step 7: Lint/type/commit.**
```bash
uv run ruff format web/db.py tests/web/test_servers_db.py
uv run ruff check web/db.py tests/web/test_servers_db.py
uv run pyright web/db.py
git add web/db.py tests/web/test_servers_db.py
git commit -m "feat(servers): servers registry table + seed + sync helpers"
```
End the commit message with a trailing `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` line.

---

### Task 2: `web/server_context.py` — registry, contextvar, middleware

**Files:** Create `web/server_context.py`; Test `tests/web/test_server_context.py`.

- [ ] **Step 1: Write failing tests** — `tests/web/test_server_context.py`:
```python
from __future__ import annotations

from web import server_context as sc


def _seed(monkeypatch, tmp_path):
    from web import db
    p = tmp_path / "users.db"
    db.init_db(p)
    monkeypatch.setattr(db, "DB_PATH", p)
    sc.load_registry()
    return p


def test_resolve_known_subdomain(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    assert sc.resolve_host("wuoshi.eq2lexicon.com").world == "Wuoshi"
    assert sc.resolve_host("varsoon.eq2lexicon.com").world == "Varsoon"


def test_resolve_unknown_falls_back_to_default(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    # default server == EQ2_WORLD (Varsoon)
    assert sc.resolve_host("localhost:8000").world == "Varsoon"
    assert sc.resolve_host("eq2lexicon.com").world == "Varsoon"
    assert sc.resolve_host("").world == "Varsoon"


def test_current_world_default_outside_request(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    # No active server set on the contextvar → default.
    assert sc.current_world() == "Varsoon"


def test_contextvar_roundtrip(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    wuoshi = sc.resolve_host("wuoshi.eq2lexicon.com")
    token = sc.set_active_server(wuoshi)
    try:
        assert sc.current_world() == "Wuoshi"
        assert sc.current_server().display_name == "Wuoshi"
    finally:
        sc.reset_active_server(token)
    assert sc.current_world() == "Varsoon"
```

- [ ] **Step 2: Run — expect fail.** `uv run pytest tests/web/test_server_context.py -q`

- [ ] **Step 3: Implement** — `web/server_context.py`:
```python
"""Per-request active-server resolution.

A single deployment serves multiple EQ2 servers, one per subdomain
(varsoon.eq2lexicon.com / wuoshi.eq2lexicon.com). Middleware resolves the
request's Host to the active server and stores it on a contextvar; the rest of
the code reads current_world()/current_server() instead of a fixed env world.
"""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send

from census.config import WORLD as _DEFAULT_WORLD
from web import db


@dataclass(frozen=True)
class Server:
    world: str
    subdomain: str
    display_name: str
    max_level: int
    current_xpac: str | None
    launch_dt: str | None


# Registry: subdomain → Server and world → Server. Loaded at startup, refreshed
# when an admin edits settings.
_by_subdomain: dict[str, Server] = {}
_by_world: dict[str, Server] = {}

_active_server: ContextVar[Server | None] = ContextVar("active_server", default=None)

# Non-production override (X-Server header / ?server= query) gate.
_ALLOW_OVERRIDE = os.getenv("ENV", "dev").lower() not in ("prod", "production")


def _to_server(row: dict) -> Server:
    return Server(
        world=row["world"],
        subdomain=row["subdomain"],
        display_name=row["display_name"],
        max_level=row["max_level"],
        current_xpac=row["current_xpac"],
        launch_dt=row["launch_dt"],
    )


def load_registry() -> None:
    """(Re)load the servers registry from the DB. Call at startup + after edits."""
    rows = db.list_servers_sync(db.DB_PATH)
    _by_subdomain.clear()
    _by_world.clear()
    for row in rows:
        srv = _to_server(row)
        _by_subdomain[srv.subdomain] = srv
        _by_world[srv.world] = srv


def default_server() -> Server:
    srv = _by_world.get(_DEFAULT_WORLD)
    if srv is not None:
        return srv
    if _by_world:
        return next(iter(_by_world.values()))
    # Registry empty (e.g. very first boot before seed) — synthesize a default.
    return Server(_DEFAULT_WORLD, _DEFAULT_WORLD.lower(), _DEFAULT_WORLD, 50, None, None)


def _subdomain_of(host: str) -> str:
    host = (host or "").split(":")[0].strip().lower()  # strip port
    if not host:
        return ""
    return host.split(".")[0]


def resolve_host(host: str, override: str | None = None) -> Server:
    """Resolve a Host header (and optional non-prod override) to a Server."""
    if override and _ALLOW_OVERRIDE:
        srv = _by_subdomain.get(override.lower()) or _by_world.get(override)
        if srv is not None:
            return srv
    srv = _by_subdomain.get(_subdomain_of(host))
    return srv if srv is not None else default_server()


def current_server() -> Server:
    return _active_server.get() or default_server()


def current_world() -> str:
    return current_server().world


def set_active_server(server: Server) -> Token:
    return _active_server.set(server)


def reset_active_server(token: Token) -> None:
    _active_server.reset(token)


def get_server() -> Server:
    """FastAPI dependency: the active server for the request."""
    return current_server()


class ServerContextMiddleware:
    """Pure-ASGI middleware: resolve Host → active server for the request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        host = headers.get("host", "")
        override = headers.get("x-server")
        if override is None:
            qs = scope.get("query_string", b"").decode()
            for part in qs.split("&"):
                if part.startswith("server="):
                    override = part[len("server="):]
                    break
        token = set_active_server(resolve_host(host, override))
        try:
            await self.app(scope, receive, send)
        finally:
            reset_active_server(token)
```

- [ ] **Step 4: Run — expect pass.** `uv run pytest tests/web/test_server_context.py -q`

- [ ] **Step 5: Lint/type/commit.**
```bash
uv run ruff format web/server_context.py tests/web/test_server_context.py
uv run ruff check web/server_context.py tests/web/test_server_context.py
uv run pyright web/server_context.py
git add web/server_context.py tests/web/test_server_context.py
git commit -m "feat(servers): Host->server resolution middleware + contextvar accessors"
```
(Trailing `Co-Authored-By:` line.)

---

### Task 3: Wire middleware + cookie domain + startup into `web/app.py`

**Files:** Modify `census/config.py`, `web/config.py`, `web/app.py`; Test `tests/web/test_server_context.py` (add an end-to-end middleware test).

- [ ] **Step 1: Add the config var** — in `census/config.py`, after `CORS_ORIGINS`:
```python
# Parent domain for the session cookie so one login spans both subdomains
# (e.g. ".eq2lexicon.com" in prod). Leave unset in dev (host-only cookie).
SESSION_COOKIE_DOMAIN: str | None = os.getenv("SESSION_COOKIE_DOMAIN") or None
```
And re-export it in `web/config.py`'s import list:
```python
from census.config import (  # noqa: F401
    CORS_ORIGINS,
    DISCORD_SYNC_GUILD_IDS,
    LAUNCH_DT_ISO,
    SERVER_MAX_LEVEL,
    SERVICE_ID,
    SESSION_COOKIE_DOMAIN,
    WORLD,
)
```

- [ ] **Step 2: Write the end-to-end test** — append to `tests/web/test_server_context.py`:
```python
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_middleware_sets_world_from_host(monkeypatch, tmp_path):
    # Build an app whose only route echoes current_world().
    from fastapi import FastAPI
    from web import server_context as sc2

    _seed(monkeypatch, tmp_path)
    app = FastAPI()
    app.add_middleware(sc2.ServerContextMiddleware)

    @app.get("/w")
    async def _w():
        return {"world": sc2.current_world()}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://wuoshi.eq2lexicon.com") as c:
        r = await c.get("/w", headers={"host": "wuoshi.eq2lexicon.com"})
    assert r.json()["world"] == "Wuoshi"
    async with AsyncClient(transport=transport, base_url="http://x") as c:
        r = await c.get("/w", headers={"host": "varsoon.eq2lexicon.com"})
    assert r.json()["world"] == "Varsoon"
```

- [ ] **Step 3: Run — expect fail** (middleware import path / not wired). `uv run pytest tests/web/test_server_context.py -q`

- [ ] **Step 4: Wire into `web/app.py`:**
  - Import near the other middleware imports: `from web.server_context import ServerContextMiddleware`, `from web import server_context`, and `from web.config import SESSION_COOKIE_DOMAIN`.
  - Add `domain=SESSION_COOKIE_DOMAIN` to the existing `SessionMiddleware` call (alongside `same_site="lax"`).
  - Register `ServerContextMiddleware` so it runs for every request and is INNER to SessionMiddleware (added before SessionMiddleware in code so it executes after the session is available — but it only reads Host, so order vs session doesn't matter; add it right after the SessionMiddleware block): `app.add_middleware(ServerContextMiddleware)`.
  - In the startup path (`_startup` in `on_startup`), after `users_db.init_db(...)` is called, add `server_context.load_registry()` so the registry is populated post-seed.

- [ ] **Step 5: Run — expect pass** (the new test + the full web suite). `uv run pytest tests/web/test_server_context.py -q` then `uv run pytest tests/web -q`

- [ ] **Step 6: Lint/type/commit.**
```bash
uv run ruff format census/config.py web/config.py web/app.py
uv run ruff check census/config.py web/config.py web/app.py
uv run pyright census/config.py web/config.py web/app.py
git add census/config.py web/config.py web/app.py tests/web/test_server_context.py
git commit -m "feat(servers): wire server-context middleware + SESSION_COOKIE_DOMAIN + registry load"
```
(Trailing `Co-Authored-By:` line.)

---

## Phase 2 — Route world-source swap (`_WORLD` → `current_world()`)

> These tasks change the *source* of the world from a fixed module constant to the request's active server. With the default server == `Varsoon`, the existing tests must stay green (single-server behaviour unchanged).

### Task 4: Swap census-lookup routes to `current_world()`

**Files:** Modify `web/routes/character.py`, `characters.py`, `guild.py`, `guild_officer.py`, `aa.py`, `web/census_refresh.py`. Test: full web suite.

- [ ] **Step 1:** In each file, replace the module-level `_WORLD` usages with a call to `current_world()`. Pattern per file:
  - Add import: `from web.server_context import current_world`.
  - Remove the `from web.config import WORLD as _WORLD` import **only if** `_WORLD` is no longer referenced after the swap (some files also use it for nothing else). If other code needs the literal default, keep the import; otherwise drop it to avoid an unused-import lint error.
  - Replace each `_WORLD` reference with `current_world()`. Common shapes: `client.get_character(name, _WORLD)` → `client.get_character(name, current_world())`; cache keys `f"...:{_WORLD.lower()}"` → `f"...:{current_world().lower()}"`.
  - In `web/census_refresh.py`, `_WORLD` appears in `request_character_refresh`/`request_guild_refresh` key building and in `_run_*_refresh`. Replace with `current_world()`. NOTE: `request_*_refresh` is called from within a request, so the contextvar is set; the spawned `asyncio.create_task` captures the world at call time — capture it into a local before the closure: in `request_character_refresh`, compute `world = current_world()` once and pass it through to `_run_character_refresh(name, key, world)` (add a `world` param) so the background task uses the captured world rather than re-reading the contextvar after the request context resets. Apply the same to the guild refresh.

- [ ] **Step 2: Verify the refresh closure capture** — edit `web/census_refresh.py` so the background world is captured explicitly:
```python
def request_character_refresh(name: str) -> None:
    world = current_world()
    key = f"{name.lower()}:{world.lower()}"
    if not _should_refresh(key):
        return
    _mark_attempt(key)
    _in_flight.add(key)
    asyncio.create_task(_run_character_refresh(name, key, world))


async def _run_character_refresh(name: str, key: str, world: str) -> None:
    from web.routes.character import _build_char_response
    try:
        client = CensusClient(service_id=_SERVICE_ID)
        try:
            char = await client.get_character(name, world)
        finally:
            await client.close()
        if char is None:
            return
        resp = _build_char_response(char)
        data = resp.model_dump()
        resolved = bool(data.get("cls") or data.get("level"))
        conn = census_store.init_db(census_store.DB_PATH)
        try:
            census_store.upsert_character(conn, name, world, data, resolved=resolved)
        finally:
            conn.close()
        if resolved:
            character_cache.set(key, resp)
            census_events.publish({"type": "character", "key": key, "data": data, "fetched_at": int(time.time())})
    except Exception as exc:
        _log.warning("[census-refresh] character %s failed: %s", _scrub(name), exc)
    finally:
        _in_flight.discard(key)
```
Apply the analogous `world` capture+param to `request_guild_refresh` / `_run_guild_refresh` (pass `world` into `_persist_and_publish_guild(name)` — see Task: update `_persist_and_publish_guild` to take an explicit `world` param defaulting to `current_world()` so the background task passes the captured world). In `web/routes/guild.py` change `async def _persist_and_publish_guild(guild_name: str)` → `async def _persist_and_publish_guild(guild_name: str, world: str | None = None)` and `world = world or current_world()` at the top, using `world` throughout instead of `current_world()`/`_WORLD`.

- [ ] **Step 3: Run the full web suite — expect green** (default server keeps Varsoon behaviour). `uv run pytest tests/web -q`

- [ ] **Step 4: Lint/type/commit.**
```bash
uv run ruff format web/routes/character.py web/routes/characters.py web/routes/guild.py web/routes/guild_officer.py web/routes/aa.py web/census_refresh.py
uv run ruff check web/routes/character.py web/routes/characters.py web/routes/guild.py web/routes/guild_officer.py web/routes/aa.py web/census_refresh.py
uv run pyright web/routes/character.py web/routes/characters.py web/routes/guild.py web/routes/guild_officer.py web/routes/aa.py web/census_refresh.py
git add web/routes/character.py web/routes/characters.py web/routes/guild.py web/routes/guild_officer.py web/routes/aa.py web/census_refresh.py
git commit -m "refactor(servers): census-lookup routes read world from request context"
```
(Trailing `Co-Authored-By:` line.)

---

### Task 5: Swap remaining per-server routes + settings reads

**Files:** Modify `web/routes/claim.py`, `item_watch.py`, `notifications.py`, `rankings.py`, `aa.py`. Test: full web suite + `tests/web/test_rankings.py`, `tests/web/test_aa.py`.

- [ ] **Step 1: `_WORLD` → `current_world()`** in `claim.py`, `item_watch.py`, `notifications.py` (same pattern as Task 4).

- [ ] **Step 2: Settings from the active server.** In `web/routes/rankings.py`, replace the `os.getenv("SERVER_CURRENT_XPAC")` read with `from web.server_context import current_server` then `env_xpac = (current_server().current_xpac or "").strip().lower()` (keep the existing short-code/full-name matching logic). In `web/routes/aa.py`, the `/aa/config` handler: replace `os.getenv("SERVER_CURRENT_XPAC")` with `current_server().current_xpac` and the `SERVER_MAX_LEVEL` usage with `current_server().max_level`.

- [ ] **Step 3: Write/adjust tests** — add to `tests/web/test_rankings.py` (or create) a test that, with the registry seeded and the active server overridden via the `X-Server` header (non-prod), the default expansion reflects that server's `current_xpac`:
```python
@pytest.mark.asyncio
async def test_rankings_default_xpac_per_server(app, monkeypatch, tmp_path):
    from web import db, server_context
    p = tmp_path / "users.db"; db.init_db(p); monkeypatch.setattr(db, "DB_PATH", p)
    db.upsert_server_settings_sync("Wuoshi", max_level=70, current_xpac="Echoes of Faydwer", launch_dt=None, path=p)
    server_context.load_registry()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/rankings/expansions", headers={"x-server": "wuoshi"})  # use the real rankings endpoint
    assert r.status_code == 200
    # assert the default selected expansion corresponds to Echoes of Faydwer
```
(Adapt the endpoint path + assertion to the real rankings route shape; the point is to prove per-server settings drive it.)

- [ ] **Step 4: Run — expect green.** `uv run pytest tests/web -q`

- [ ] **Step 5: Lint/type/commit.**
```bash
uv run ruff format web/routes/claim.py web/routes/item_watch.py web/routes/notifications.py web/routes/rankings.py web/routes/aa.py tests/web/test_rankings.py
uv run ruff check <same files>
uv run pyright web/routes/claim.py web/routes/item_watch.py web/routes/notifications.py web/routes/rankings.py web/routes/aa.py
git add web/routes/claim.py web/routes/item_watch.py web/routes/notifications.py web/routes/rankings.py web/routes/aa.py tests/web/test_rankings.py
git commit -m "feat(servers): per-server settings drive rankings/aa; remaining routes use request world"
```
(Trailing `Co-Authored-By:` line.)

---

## Phase 3 — Per-server data scoping

### Task 6: `character_claims.world` + per-`(user, world)` claims

**Files:** Modify `web/db.py`, `web/routes/claim.py` (+ any claim helpers in `web/db.py`); Test `tests/web/test_claim*.py`.

- [ ] **Step 1: Read the current claim helpers** in `web/db.py` (`get_active_claims`, claim insert/list, primary-character selection using `is_primary`). Note their exact signatures.

- [ ] **Step 2: Migrate the column** — in `web/db.py` `init_db`, extend the `character_claims` migration block:
```python
        if "world" not in claims_cols:
            conn.execute("ALTER TABLE character_claims ADD COLUMN world TEXT NOT NULL DEFAULT 'Varsoon'")
```
(`claims_cols` is already computed above for `is_primary`.)

- [ ] **Step 3: Write failing tests** — `tests/web/test_claim_per_server.py`: a user's approved claim under `Varsoon` is NOT returned when querying world `Wuoshi`; `is_primary` is independent per `(discord_id, world)`. (Use the claim helpers directly with a `world` arg.)

- [ ] **Step 4: Thread `world` through the claim helpers** in `web/db.py` — add a `world: str` parameter to the claim read/write/primary helpers (`get_active_claims(discord_id, world, ...)`, the insert, the "set primary" that clears other primaries scoped to the same world, the list-by-status used by admin). Every claim query gains `AND world = ?`; every insert sets `world`; "set primary" clears `is_primary` only `WHERE discord_id = ? AND world = ?`.

- [ ] **Step 5: Pass `current_world()` from the routes** — in `web/routes/claim.py`, every call into the claim helpers passes `current_world()` (import it). Submitting a claim records the active world; listing my claims / primary is scoped to the active world.

- [ ] **Step 6: Run — expect pass** (new + existing claim tests; existing tests default to Varsoon so still pass). `uv run pytest tests/web -q`

- [ ] **Step 7: Lint/type/commit.**
```bash
git add web/db.py web/routes/claim.py tests/web/test_claim_per_server.py
git commit -m "feat(servers): scope character claims + primary character per (user, world)"
```
(ruff/pyright first; trailing `Co-Authored-By:` line.)

---

### Task 7: `item_watch.world` (table rebuild for uniqueness)

**Files:** Modify `web/db.py`, `web/routes/item_watch.py`; Test `tests/web/test_item_watch.py`.

- [ ] **Step 1: Failing test** — same `(guild_name, character_name, item_id)` can be watched independently on two worlds; a watch created under `Varsoon` is invisible under `Wuoshi`.

- [ ] **Step 2: Rebuild migration** — the inline `UNIQUE(guild_name, character_name, item_id)` must become `UNIQUE(world, guild_name, character_name, item_id)`. SQLite can't alter a table constraint in place, so in `web/db.py` `init_db` add an idempotent rebuild guarded on the column:
```python
        watch_cols = {row[1] for row in conn.execute("PRAGMA table_info(item_watch)")}
        if "world" not in watch_cols:
            conn.executescript(
                """
                ALTER TABLE item_watch RENAME TO item_watch_old;
                CREATE TABLE item_watch (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    world           TEXT    NOT NULL DEFAULT 'Varsoon',
                    guild_name      TEXT    NOT NULL,
                    character_name  TEXT    NOT NULL,
                    item_id         INTEGER NOT NULL,
                    item_name       TEXT    NOT NULL,
                    added_by        TEXT    NOT NULL REFERENCES users(discord_id),
                    added_by_name   TEXT    NOT NULL,
                    added_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    first_seen_at   INTEGER,
                    last_seen_at    INTEGER,
                    last_checked_at INTEGER,
                    UNIQUE(world, guild_name, character_name, item_id)
                );
                INSERT INTO item_watch (id, world, guild_name, character_name, item_id, item_name,
                                        added_by, added_by_name, added_at, first_seen_at, last_seen_at, last_checked_at)
                    SELECT id, 'Varsoon', guild_name, character_name, item_id, item_name,
                           added_by, added_by_name, added_at, first_seen_at, last_seen_at, last_checked_at
                    FROM item_watch_old;
                DROP TABLE item_watch_old;
                CREATE INDEX IF NOT EXISTS idx_watch_guild ON item_watch(guild_name);
                """
            )
```
Also update the inline `_SCHEMA` `CREATE TABLE item_watch` to the new shape (with `world` + the new UNIQUE) so fresh DBs get it directly. Keep both in sync.

- [ ] **Step 3: World-scope the queries** — add `world` to the item-watch insert + every SELECT/UPDATE in `web/db.py` item-watch helpers; routes in `web/routes/item_watch.py` pass `current_world()`.

- [ ] **Step 4: Run — expect pass.** `uv run pytest tests/web -q`

- [ ] **Step 5: Lint/type/commit.**
```bash
git add web/db.py web/routes/item_watch.py tests/web/test_item_watch.py
git commit -m "feat(servers): scope item-watch per server (world column + rebuilt uniqueness)"
```
(ruff/pyright first; trailing `Co-Authored-By:` line.)

---

### Task 8: `encounters.world` + parse attribution + scoped reads

**Files:** Modify `parses/db.py`, `web/routes/parses.py`, `web/routes/rankings.py`; Test `tests/parses/test_db.py`, `tests/web/test_parses*.py`.

- [ ] **Step 1: Read** `parses/db.py` (the `encounters` schema, `is_ingested`, `insert_encounter`, `recent_encounters`, `find_encounter_by_act_encid`) and `web/routes/parses.py` (`_sanitize_world`, `_resolve_uploader_guild_async`, the ingest insert path).

- [ ] **Step 2: Failing tests** in `tests/parses/test_db.py`: two encounters with the SAME `act_encid` but different `world` both insert (no collision); `is_ingested(world, act_encid)` is world-scoped; `recent_encounters(world=...)` filters by world.

- [ ] **Step 3: Migrate `encounters`** — add a `world` column and change uniqueness from `act_encid UNIQUE` to `(world, act_encid)`. Since `act_encid` has an inline `UNIQUE`, use the rebuild pattern (rename → create new with `world TEXT NOT NULL DEFAULT 'Varsoon'` and `UNIQUE(world, act_encid)` + the other columns/indexes → copy with `'Varsoon'` → drop old). Add this to `parses/db.py` `init_db` guarded on `"world" not in encounters_cols`, and update the `_CREATE_ENCOUNTERS` constant to the new shape for fresh DBs. (Follow the existing `parses/db.py` migration/`_MIGRATIONS` conventions; the `ingest_log` idempotency table, if it keys on `act_encid`, also gets `world` — scope its PK/unique to `(world, act_encid)` via the same rebuild.)

- [ ] **Step 4: Thread `world`** — `is_ingested`, `insert_encounter`, `recent_encounters`, `find_encounter_by_act_encid`, and the ingest-log helpers gain a `world` parameter; queries filter/insert by it.

- [ ] **Step 5: Attribution in ingest** — in `web/routes/parses.py` `ingest_parse`, resolve the parse world from `logger_server` (the existing sanitized value) mapped to a known registry world via `web.server_context` (`get_by_world`/registry); if absent/unknown, fall back to `current_world()`. Persist it on the encounter; dedupe via `is_ingested(world, act_encid)`.

- [ ] **Step 6: Scope reads** — parse listing / encounter detail endpoints and `web/routes/rankings.py` filter by `current_world()`; ranking kill-qualification uses `current_server().max_level`.

- [ ] **Step 7: Run — expect green** (parses + web suites). `uv run pytest tests/parses tests/web -q`

- [ ] **Step 8: Lint/type/commit.**
```bash
git add parses/db.py web/routes/parses.py web/routes/rankings.py tests/parses/test_db.py tests/web/test_parses*.py
git commit -m "feat(servers): scope parses per server (world column, logger_server attribution, (world,act_encid) idempotency)"
```
(ruff/pyright first; trailing `Co-Authored-By:` line.)

---

## Phase 4 — Admin + settings editor

### Task 9: Per-server admin scoping + settings endpoints

**Files:** Modify `web/routes/admin.py` (+ claim/parse admin helpers); Test `tests/web/test_admin*.py`.

- [ ] **Step 1: Read** `web/routes/admin.py` — the claim-review list, parse list/delete, and role/approval handlers.

- [ ] **Step 2: Failing tests** — admin claim list on the active server returns only that world's claims; parse delete only affects the active world's parses; role/access approval endpoints are unchanged (universal).

- [ ] **Step 3: Scope claim + parse admin** to `current_world()` (pass it into the claim list helper from Task 6 and the parse list/delete from Task 8). Leave user-approval + role endpoints untouched.

- [ ] **Step 4: Settings editor** — add admin endpoints:
  - `GET /api/admin/servers` → `db.list_servers_sync()`.
  - `PUT /api/admin/servers/{world}` (admin-only) → validates `max_level` (int), `current_xpac` (str|null), `launch_dt` (ISO str|null); calls `db.upsert_server_settings_sync(...)` then `server_context.load_registry()`. Returns the updated server.
  Add a pydantic body model `ServerSettingsUpdate`.

- [ ] **Step 5: Run — expect green.** `uv run pytest tests/web -q`

- [ ] **Step 6: Lint/type/commit.**
```bash
git add web/routes/admin.py tests/web/test_admin_servers.py
git commit -m "feat(servers): per-server admin claim/parse scoping + settings editor"
```
(ruff/pyright first; trailing `Co-Authored-By:` line.)

---

## Phase 5 — Frontend

### Task 10: `GET /api/server` endpoint

**Files:** Create `web/routes/server.py`; Modify `web/app.py` (register router); Test `tests/web/test_server_route.py`.

- [ ] **Step 1: Failing test** — `GET /api/server` with `Host: wuoshi.eq2lexicon.com` returns `{world:"Wuoshi", display_name, max_level, current_xpac, launch_dt}`; with an unknown host returns the default (Varsoon).

- [ ] **Step 2: Implement** — `web/routes/server.py`:
```python
"""Active-server info for the current subdomain (frontend bootstrap)."""

from __future__ import annotations

from fastapi import APIRouter

from web.server_context import current_server, list_public_servers

router = APIRouter(tags=["server"])


@router.get("/server")
async def get_active_server() -> dict:
    s = current_server()
    return {
        "world": s.world,
        "display_name": s.display_name,
        "max_level": s.max_level,
        "current_xpac": s.current_xpac,
        "launch_dt": s.launch_dt,
        "servers": list_public_servers(),  # [{world, subdomain, display_name}] for the switcher
    }
```
Add `list_public_servers()` to `web/server_context.py`:
```python
def list_public_servers() -> list[dict]:
    return [
        {"world": s.world, "subdomain": s.subdomain, "display_name": s.display_name}
        for s in _by_world.values()
    ]
```
Register in `web/app.py`: `from web.routes.server import router as server_router` + `app.include_router(server_router, prefix="/api")`.

- [ ] **Step 3: Run — expect pass.** `uv run pytest tests/web/test_server_route.py -q` then `uv run pytest tests/web -q`

- [ ] **Step 4: Lint/type/commit.**
```bash
git add web/routes/server.py web/server_context.py web/app.py tests/web/test_server_route.py
git commit -m "feat(servers): GET /api/server (active server + switcher list)"
```
(ruff/pyright first; trailing `Co-Authored-By:` line.)

---

### Task 11: Frontend server context + header + timer/AA caps

**Files:** Create `frontend/src/hooks/useServer.tsx`; Modify `frontend/src/App.tsx`, the launch-timer + AA-cap consumers.

- [ ] **Step 1: Implement `useServer`** — `frontend/src/hooks/useServer.tsx`: a context provider that fetches `/api/server` once on mount and exposes `{ world, displayName, maxLevel, currentXpac, launchDt, servers }` (loading-tolerant). Match the repo's existing hook/context conventions.

- [ ] **Step 2: Header** — in `frontend/src/App.tsx`, wrap the app in `<ServerProvider>`; show the active server's `display_name` in the header; add a **switch-server** link/menu built from `servers` (links to `https://{subdomain}.eq2lexicon.com` — derive the base domain from `window.location.host`).

- [ ] **Step 3: Timer + AA caps** — `ServerLaunchTimer` reads `launchDt` from `useServer()` instead of the build-time env; the AA-cap display reads `maxLevel`/`currentXpac` from `useServer()` where it currently relies on `/api/aa/config` or env. (Keep `/api/aa/config` as the source for AA-tree unlock data; only the max-level/xpac display switches to `useServer`.)

- [ ] **Step 4: Typecheck + build.** `cd frontend; npm run typecheck && npm run build` — 0 errors.

- [ ] **Step 5: Commit** (visual — confirm with the user per the hold-commits rule before committing if it changes visible layout; otherwise commit):
```bash
git add frontend/src/hooks/useServer.tsx frontend/src/App.tsx <timer + aa-cap files>
git commit -m "feat(servers): frontend active-server context, header name + switcher, per-server timer/caps"
```
(Trailing `Co-Authored-By:` line.)

---

## Phase 6 — Docs + full gate

### Task 12: Docs + repo-wide gate

**Files:** Modify `CLAUDE.md`, `.env.example`.

- [ ] **Step 1: Docs** — in `CLAUDE.md` + `.env.example`:
  - `EQ2_WORLD` — now the **default-server** selector (fallback/dev), not the only server.
  - `SERVER_MAX_LEVEL` / `SERVER_CURRENT_XPAC` / `LAUNCH_DT` — **retired from runtime**; now per-server in the `servers` table (admin-editable). Note they seed the Varsoon row on first migration.
  - `SESSION_COOKIE_DOMAIN` — **new**, parent domain for cross-subdomain login (`.eq2lexicon.com` in prod; unset in dev).
  - Add a "Per-server architecture" note: Host → `server_context` → `current_world()`; the `servers` registry; per-server claims/item-watch/parses; universal users/roles.

- [ ] **Step 2: Full gate** (everything green):
```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
cd frontend && npm run typecheck && npm run build
```

- [ ] **Step 3: Commit + finish.**
```bash
git add CLAUDE.md .env.example
git commit -m "docs: per-server URLs (servers registry, SESSION_COOKIE_DOMAIN, EQ2_WORLD as default)"
```
Then invoke `superpowers:finishing-a-development-branch`.

---

## Rollout notes (post-merge)
- Confirm Railway serves BOTH custom domains (`varsoon.` + `wuoshi.`) from this one deployment.
- Set `SESSION_COOKIE_DOMAIN=.eq2lexicon.com`. Migrations auto-add the `world` columns, backfill `Varsoon`, and seed the `servers` table (Varsoon from current env, Wuoshi default).
- In admin, set Wuoshi's `max_level` / `current_xpac` / `launch_dt`.
- Verify: Varsoon behaves exactly as before; Wuoshi shows its own (initially empty) per-server data; one login works on both subdomains.

---

## Self-review (against the spec)
- §1 server resolution → Tasks 2–3 (middleware + contextvar + `current_world()`), Task 4–5 (swaps). ✓
- §2 servers registry + per-server settings → Task 1 (table+seed), Task 5 (rankings/aa read), Task 9 (editor). ✓
- §3 data scoping → Task 6 (claims), Task 7 (item_watch), Task 8 (parses); census_store already keyed. ✓
- §4 auth/roles/admin → Task 3 (cookie domain), Task 9 (per-server claim/parse admin; universal roles untouched). ✓
- §5 parses attribution → Task 8 (logger_server → world, `(world,act_encid)`). ✓
- §6 frontend → Task 10 (`/api/server`), Task 11 (context/header/switcher/timer/caps). ✓
- §7 migration & dev → backfill `Varsoon` in Tasks 6–8; default-server fallback + `X-Server`/`?server=` in Task 2; `SESSION_COOKIE_DOMAIN` unset in dev in Task 3. ✓
- Bot stays single-server (out of scope) — no bot tasks. ✓
