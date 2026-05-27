# Census Persistence & Resilient Caching — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make guild/character pages serve last-known data instantly and never break when Census is down, by adding a persistent store, background-only refresh, an SSE live-update channel, and a site-wide Census-health indicator.

**Architecture:** Three read layers — in-memory `TTLCache` (hot path) → persistent `census.db` (durable, survives deploys) → Census (background refresh only, never on the request path). Refreshes merge "keep-best-known" (never null-out good data), are throttled + deduped + skipped when Census is down, and publish over an in-process SSE pub/sub that the frontend uses to swap data live and drive a footer health dot.

**Tech Stack:** Python 3.13, FastAPI, stdlib `sqlite3` (WAL), `aiohttp` (existing `CensusClient`), Server-Sent Events; React 19 + TypeScript + Vite + Tailwind v4. Tests: `pytest`/`pytest-asyncio`, `tsc`. Tooling: `uv run …`, `ruff`, `pyright`.

Spec: `docs/superpowers/specs/2026-05-27-census-persistence-caching-design.md`.

---

## File Structure

**New (backend):**
- `census/census_store.py` — persistent SQLite store: schema, `init_db`, merge-upsert + get for characters & guilds. Pure data layer (no Census, no FastAPI). Mirrors `parses/db.py`.
- `web/census_health.py` — in-memory Census-health state + a `check_census()` probe + a 5-minute poll loop.
- `web/census_events.py` — in-process async pub/sub for SSE (subscribe/publish).
- `web/census_refresh.py` — refresh orchestration: throttle (in-memory last-attempt), in-flight dedupe, health gate; calls `CensusClient`, merges into `census_store`, updates the in-memory cache, publishes SSE. The single place routes call to trigger a background refresh.
- `web/routes/census.py` — `GET /api/census/health` + `GET /api/census/stream` (SSE).

**New (frontend):**
- `frontend/src/hooks/useCensusStream.tsx` — app-level `EventSource` context: exposes `health` and a `subscribe(key, cb)` for record updates.
- `frontend/src/components/CensusStatus.tsx` — footer green/red dot.
- `frontend/src/components/FreshnessBadge.tsx` — "Updating from Census…" / "Census unavailable — showing stored data".

**Modified:**
- `web/routes/character.py` — read path serves `census_store` on miss (no sync Census); response gains `fetched_at`+`stale`; refresh routed through `census_refresh`.
- `web/routes/guild.py` — `_fetch_and_cache_guild` persists guild + merge-upserts resolved members into `census_store` and rebuilds roster from best-known; read paths serve `census_store`; refresh routed through `census_refresh`.
- `web/app.py` — register census router; start the health poll loop.
- `frontend/src/App.tsx` — wrap app in `CensusStreamProvider`; add `<CensusStatus/>` to the footer.
- `frontend/src/pages/CharacterPage.tsx`, `frontend/src/pages/GuildPage.tsx` — render `FreshnessBadge`; subscribe to SSE to live-swap.

**Tests:** `tests/census/test_census_store.py`, `tests/web/test_census_health.py`, `tests/web/test_census_events.py`, `tests/web/test_census_route.py`, plus additions to `tests/web/test_character.py` and `tests/web/test_guild.py`.

**Conventions:** run everything with `uv run …` (uv is on PATH). After each task: `uv run ruff format <files>`, `uv run ruff check <files>`, `uv run pyright <files>` (backend) / `npx tsc -b` in `frontend/` (frontend). Commit per task. Do **not** commit `data/census/census.db` (add to `.gitignore`).

---

## Phase 1 — Persistent store (`census/census_store.py`)

### Task 1: DB scaffold + schema

**Files:**
- Create: `census/census_store.py`
- Create: `tests/census/test_census_store.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write the failing test**

```python
# tests/census/test_census_store.py
from __future__ import annotations

from census import census_store as cs


def test_init_db_creates_tables(tmp_path):
    conn = cs.init_db(tmp_path / "census.db")
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"characters", "guilds"} <= tables
        char_cols = {r[1] for r in conn.execute("PRAGMA table_info(characters)")}
        assert {"name_lower", "world", "name", "level", "guild_name", "data_json", "last_resolved_at", "updated_at"} <= char_cols
    finally:
        conn.close()
```

- [ ] **Step 2: Run it — expect failure**

Run: `uv run pytest tests/census/test_census_store.py -q`
Expected: FAIL (`ModuleNotFoundError: census.census_store`).

- [ ] **Step 3: Implement the scaffold**

```python
# census/census_store.py
"""Persistent, deploy-surviving store of the last-known character + guild
lookups. The web request path serves from here (via the in-memory cache) and
never blocks on Census; background refreshes merge in fresh data "keep best
known" — a sparse Census response never nulls out good data.

Mirrors parses/db.py: CENSUS_DB_PATH env override, WAL, idempotent _MIGRATIONS.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path


def _db_path() -> Path:
    env = os.getenv("CENSUS_DB_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "census" / "census.db"


DB_PATH: Path = _db_path()

_CREATE_CHARACTERS = """
CREATE TABLE IF NOT EXISTS characters (
    name_lower       TEXT    NOT NULL,
    world            TEXT    NOT NULL,
    name             TEXT    NOT NULL,
    level            INTEGER,
    guild_name       TEXT,
    data_json        TEXT    NOT NULL,
    last_resolved_at INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    PRIMARY KEY (name_lower, world)
);
"""

_CREATE_GUILDS = """
CREATE TABLE IF NOT EXISTS guilds (
    name_lower       TEXT    NOT NULL,
    world            TEXT    NOT NULL,
    name             TEXT    NOT NULL,
    data_json        TEXT    NOT NULL,
    last_resolved_at INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    PRIMARY KEY (name_lower, world)
);
"""

_MIGRATIONS: list[str] = []  # future schema bumps appended here


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute(_CREATE_CHARACTERS)
    conn.execute(_CREATE_GUILDS)
    for stmt in _MIGRATIONS:
        conn.execute(stmt)
    conn.commit()
    return conn
```

- [ ] **Step 4: Run it — expect pass**

Run: `uv run pytest tests/census/test_census_store.py -q`
Expected: PASS.

- [ ] **Step 5: Gitignore the DB**

Append to `.gitignore`:
```
# Census persistence (generated at runtime, copied to the Railway volume)
data/census/census.db
data/census/census.db-wal
data/census/census.db-shm
```

- [ ] **Step 6: Commit**

```bash
git add census/census_store.py tests/census/test_census_store.py .gitignore
git commit -m "feat(census): persistent store scaffold (characters + guilds)"
```

---

### Task 2: Character merge-upsert + get (keep-best-known)

**Files:**
- Modify: `census/census_store.py`
- Test: `tests/census/test_census_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/census/test_census_store.py  (append)
def test_upsert_character_resolved_then_get(tmp_path):
    conn = cs.init_db(tmp_path / "census.db")
    try:
        cs.upsert_character(conn, "Menludiir", "Varsoon",
                            {"name": "Menludiir", "level": 90, "guild_name": "Exordium", "cls": "Templar"},
                            resolved=True, now=1000)
        rec = cs.get_character(conn, "Menludiir", "Varsoon")
        assert rec is not None
        assert rec["data"]["level"] == 90
        assert rec["last_resolved_at"] == 1000
    finally:
        conn.close()


def test_sparse_refresh_never_clobbers(tmp_path):
    conn = cs.init_db(tmp_path / "census.db")
    try:
        cs.upsert_character(conn, "Menludiir", "Varsoon",
                            {"name": "Menludiir", "level": 90, "cls": "Templar"}, resolved=True, now=1000)
        # A later sparse fetch (didn't resolve) must NOT overwrite the good row.
        cs.upsert_character(conn, "Menludiir", "Varsoon",
                            {"name": "Menludiir", "level": None, "cls": None}, resolved=False, now=2000)
        rec = cs.get_character(conn, "Menludiir", "Varsoon")
        assert rec["data"]["level"] == 90        # kept
        assert rec["last_resolved_at"] == 1000    # unchanged
    finally:
        conn.close()


def test_unresolved_first_sight_is_not_stored(tmp_path):
    conn = cs.init_db(tmp_path / "census.db")
    try:
        cs.upsert_character(conn, "Ghost", "Varsoon", {"name": "Ghost"}, resolved=False, now=1000)
        assert cs.get_character(conn, "Ghost", "Varsoon") is None
    finally:
        conn.close()


def test_get_missing_returns_none(tmp_path):
    conn = cs.init_db(tmp_path / "census.db")
    try:
        assert cs.get_character(conn, "Nobody", "Varsoon") is None
    finally:
        conn.close()
```

- [ ] **Step 2: Run — expect fail** (`AttributeError: upsert_character`).

- [ ] **Step 3: Implement**

```python
# census/census_store.py  (append)

def upsert_character(
    conn: sqlite3.Connection,
    name: str,
    world: str,
    data: dict,
    *,
    resolved: bool,
    now: int | None = None,
) -> None:
    """Merge-store a character. When ``resolved`` is False the call is a no-op
    (keep best-known: never overwrite a good row with a sparse one, and never
    insert a sparse first-sight row). When True, replace the record + stamp
    last_resolved_at."""
    if not resolved:
        return
    ts = int(time.time()) if now is None else now
    conn.execute(
        """
        INSERT INTO characters (name_lower, world, name, level, guild_name, data_json, last_resolved_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name_lower, world) DO UPDATE SET
            name=excluded.name, level=excluded.level, guild_name=excluded.guild_name,
            data_json=excluded.data_json, last_resolved_at=excluded.last_resolved_at,
            updated_at=excluded.updated_at
        """,
        (name.lower(), world, name, data.get("level"), data.get("guild_name"),
         json.dumps(data), ts, ts),
    )
    conn.commit()


def get_character(conn: sqlite3.Connection, name: str, world: str) -> dict | None:
    """Return {data, last_resolved_at} or None."""
    row = conn.execute(
        "SELECT data_json, last_resolved_at FROM characters WHERE name_lower=? AND world=?",
        (name.lower(), world),
    ).fetchone()
    if row is None:
        return None
    return {"data": json.loads(row[0]), "last_resolved_at": row[1]}
```

- [ ] **Step 4: Run — expect pass.**

- [ ] **Step 5: Commit**

```bash
git add census/census_store.py tests/census/test_census_store.py
git commit -m "feat(census): keep-best-known character upsert + get"
```

---

### Task 3: Guild upsert + get

**Files:**
- Modify: `census/census_store.py`
- Test: `tests/census/test_census_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/census/test_census_store.py  (append)
def test_upsert_guild_then_get(tmp_path):
    conn = cs.init_db(tmp_path / "census.db")
    try:
        blob = {"name": "Exordium", "members": [{"name": "Menludiir", "rank": "Leader"}]}
        cs.upsert_guild(conn, "Exordium", "Varsoon", blob, now=1000)
        rec = cs.get_guild(conn, "Exordium", "Varsoon")
        assert rec["data"]["members"][0]["name"] == "Menludiir"
        assert rec["last_resolved_at"] == 1000
    finally:
        conn.close()


def test_guild_get_missing_returns_none(tmp_path):
    conn = cs.init_db(tmp_path / "census.db")
    try:
        assert cs.get_guild(conn, "Nope", "Varsoon") is None
    finally:
        conn.close()
```

- [ ] **Step 2: Run — expect fail.**

- [ ] **Step 3: Implement**

```python
# census/census_store.py  (append)

def upsert_guild(conn: sqlite3.Connection, name: str, world: str, data: dict, *, now: int | None = None) -> None:
    """Store the guild roster blob (member names+ranks + info). Always replaces —
    the roster list is reliable from Census regardless of member login recency."""
    ts = int(time.time()) if now is None else now
    conn.execute(
        """
        INSERT INTO guilds (name_lower, world, name, data_json, last_resolved_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(name_lower, world) DO UPDATE SET
            name=excluded.name, data_json=excluded.data_json,
            last_resolved_at=excluded.last_resolved_at, updated_at=excluded.updated_at
        """,
        (name.lower(), world, name, json.dumps(data), ts, ts),
    )
    conn.commit()


def get_guild(conn: sqlite3.Connection, name: str, world: str) -> dict | None:
    row = conn.execute(
        "SELECT data_json, last_resolved_at FROM guilds WHERE name_lower=? AND world=?",
        (name.lower(), world),
    ).fetchone()
    if row is None:
        return None
    return {"data": json.loads(row[0]), "last_resolved_at": row[1]}
```

- [ ] **Step 4: Run — expect pass.**

- [ ] **Step 5: Commit**

```bash
git add census/census_store.py tests/census/test_census_store.py
git commit -m "feat(census): guild roster upsert + get"
```

---

## Phase 2 — Census health (`web/census_health.py`)

### Task 4: Health state + probe + poll loop

**Files:**
- Create: `web/census_health.py`
- Create: `tests/web/test_census_health.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/web/test_census_health.py
from __future__ import annotations

import pytest

from web import census_health as ch


def test_initial_state_is_unknown_up():
    ch._reset_for_test()
    s = ch.get_state()
    assert s["status"] in ("up", "unknown")
    assert "checked_at" in s


@pytest.mark.asyncio
async def test_probe_marks_up_on_200(monkeypatch):
    ch._reset_for_test()

    async def fake_probe() -> bool:
        return True

    monkeypatch.setattr(ch, "_probe_census", fake_probe)
    await ch.refresh_health()
    assert ch.get_state()["status"] == "up"


@pytest.mark.asyncio
async def test_probe_marks_down_on_failure(monkeypatch):
    ch._reset_for_test()

    async def fake_probe() -> bool:
        return False

    monkeypatch.setattr(ch, "_probe_census", fake_probe)
    await ch.refresh_health()
    assert ch.get_state()["status"] == "down"
    assert ch.is_down() is True
```

- [ ] **Step 2: Run — expect fail.**

- [ ] **Step 3: Implement**

```python
# web/census_health.py
"""Site-wide Census availability signal. A background loop probes Census every
5 minutes; the request/refresh paths read this (live, in-memory) state to decide
whether to attempt a refresh and what to tell the user."""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from web.config import SERVICE_ID as _SERVICE_ID

_log = logging.getLogger(__name__)

_POLL_INTERVAL = 300  # 5 minutes
_PROBE_URL = f"https://census.daybreakgames.com/s:{_SERVICE_ID}/json/get/eq2/"

_status: str = "unknown"   # "up" | "down" | "unknown"
_checked_at: int = 0


def _reset_for_test() -> None:
    global _status, _checked_at
    _status, _checked_at = "unknown", 0


def get_state() -> dict:
    return {"status": _status, "checked_at": _checked_at}


def is_down() -> bool:
    return _status == "down"


async def _probe_census() -> bool:
    """True if Census answers 200 within a short timeout, else False."""
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s, s.get(_PROBE_URL) as r:
            return r.status == 200
    except Exception:
        return False


async def refresh_health() -> str:
    """Probe once, update state, return the new status. Publishes an SSE health
    event on change (import is local to avoid a cycle)."""
    global _status, _checked_at
    ok = await _probe_census()
    new = "up" if ok else "down"
    changed = new != _status
    _status, _checked_at = new, int(time.time())
    if changed:
        from web import census_events
        census_events.publish({"type": "health", "status": _status, "checked_at": _checked_at})
    return _status


async def poll_loop() -> None:
    """Background task: probe now, then every 5 minutes. Never raises."""
    while True:
        try:
            await refresh_health()
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("[census-health] probe error: %s", exc)
        await asyncio.sleep(_POLL_INTERVAL)
```

- [ ] **Step 4: Run — expect pass.**

(`census_events.publish` is referenced via a local import; Task 5 creates it. The health tests don't trigger a change-publish unless status flips from a non-equal value — `_reset_for_test` sets `unknown`, so the first `up`/`down` *is* a change and will import `census_events`. To keep Task 4 self-contained, create a stub now and flesh it out in Task 5.)

- [ ] **Step 5: Create the events stub so the publish import resolves**

```python
# web/census_events.py
"""In-process pub/sub for SSE. Single-process only (see spec caveat)."""
from __future__ import annotations


def publish(event: dict) -> None:  # fleshed out in Task 5
    pass
```

- [ ] **Step 6: Re-run health tests — expect pass.**

Run: `uv run pytest tests/web/test_census_health.py -q`

- [ ] **Step 7: Commit**

```bash
git add web/census_health.py web/census_events.py tests/web/test_census_health.py
git commit -m "feat(census): health probe + 5-min poll loop"
```

---

## Phase 3 — SSE pub/sub + route

### Task 5: In-process pub/sub (`web/census_events.py`)

**Files:**
- Modify: `web/census_events.py`
- Create: `tests/web/test_census_events.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/web/test_census_events.py
from __future__ import annotations

import asyncio

import pytest

from web import census_events as ev


@pytest.mark.asyncio
async def test_subscriber_receives_published_event():
    ev._reset_for_test()
    q = ev.subscribe()
    try:
        ev.publish({"type": "character", "key": "menludiir:varsoon", "data": {"level": 90}})
        got = await asyncio.wait_for(q.get(), timeout=1)
        assert got["type"] == "character"
        assert got["data"]["level"] == 90
    finally:
        ev.unsubscribe(q)


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    ev._reset_for_test()
    q = ev.subscribe()
    ev.unsubscribe(q)
    ev.publish({"type": "health", "status": "down"})
    assert q.empty()
```

- [ ] **Step 2: Run — expect fail** (`subscribe` not defined).

- [ ] **Step 3: Implement**

```python
# web/census_events.py
"""In-process async pub/sub backing the SSE stream. Each SSE connection holds a
subscriber Queue; refresh + health events fan out to all of them.

SINGLE-PROCESS ONLY: events published in one process aren't seen by another. The
app runs as one uvicorn process today; multiple workers would need a broker."""

from __future__ import annotations

import asyncio

_subscribers: set[asyncio.Queue] = set()


def _reset_for_test() -> None:
    _subscribers.clear()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(event: dict) -> None:
    """Non-blocking fan-out. A full/slow subscriber queue drops the event for
    that subscriber rather than blocking refreshers."""
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
```

- [ ] **Step 4: Run — expect pass.**

- [ ] **Step 5: Commit**

```bash
git add web/census_events.py tests/web/test_census_events.py
git commit -m "feat(census): in-process SSE pub/sub"
```

---

### Task 6: Census route — `/health` + `/stream`

**Files:**
- Create: `web/routes/census.py`
- Modify: `web/app.py` (register router)
- Create: `tests/web/test_census_route.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_census_route.py
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from web import census_health as ch
from web.app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.mark.asyncio
async def test_health_endpoint(app):
    ch._reset_for_test()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/census/health")
    assert r.status_code == 200
    assert r.json()["status"] in ("up", "down", "unknown")
```

- [ ] **Step 2: Run — expect fail** (404 — route not registered).

- [ ] **Step 3: Implement the route**

```python
# web/routes/census.py
"""Census availability endpoints: a JSON health snapshot for first paint, and an
SSE stream that pushes refresh records + health changes to the browser."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from web import census_events, census_health

router = APIRouter(tags=["census"])


@router.get("/census/health")
async def get_census_health() -> dict:
    return census_health.get_state()


@router.get("/census/stream")
async def census_stream(request: Request) -> StreamingResponse:
    async def gen():
        q = census_events.subscribe()
        # Prime the client with the current health snapshot.
        yield _sse({"type": "health", **census_health.get_state()})
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=20)
                    yield _sse(event)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"  # comment ping survives proxy idle timeouts
        finally:
            census_events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"
```

- [ ] **Step 4: Register the router in `web/app.py`**

Add the import alongside the other route imports (near line 42, with `from web.routes.character import …`):
```python
from web.routes.census import router as census_router
```
Add the include alongside the other `app.include_router(...)` calls (the block near line 285):
```python
    app.include_router(census_router, prefix="/api")
```

- [ ] **Step 5: Run — expect pass.**

Run: `uv run pytest tests/web/test_census_route.py -q`

- [ ] **Step 6: Start the health poll loop at startup**

In `web/app.py`, inside the existing `async def _prewarm()` (near line 222, which already does `_asyncio.create_task(prewarm_character_cache())`), add:
```python
        from web import census_health
        _asyncio.create_task(census_health.poll_loop())
```

- [ ] **Step 7: Commit**

```bash
git add web/routes/census.py web/app.py tests/web/test_census_route.py
git commit -m "feat(census): /census/health + /census/stream (SSE) + health poll on startup"
```

---

## Phase 4 — Refresh orchestration (`web/census_refresh.py`)

### Task 7: Character refresh orchestrator

**Files:**
- Create: `web/census_refresh.py`
- Create: `tests/web/test_census_refresh.py`

The orchestrator centralises: throttle (in-memory last-attempt, 15 min), in-flight dedupe, skip-when-health-down, fetch → merge into `census_store` → update in-memory cache → publish SSE. Routes call `request_character_refresh(name)`; they never touch Census directly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/web/test_census_refresh.py
from __future__ import annotations

import pytest

from web import census_refresh as cr


def test_should_refresh_respects_throttle(monkeypatch):
    cr._reset_for_test()
    monkeypatch.setattr(cr.census_health, "is_down", lambda: False)
    key = "menludiir:varsoon"
    assert cr._should_refresh(key) is True
    cr._mark_attempt(key)
    assert cr._should_refresh(key) is False  # within 15 min


def test_should_refresh_skips_when_down(monkeypatch):
    cr._reset_for_test()
    monkeypatch.setattr(cr.census_health, "is_down", lambda: True)
    assert cr._should_refresh("anykey:varsoon") is False
```

- [ ] **Step 2: Run — expect fail.**

- [ ] **Step 3: Implement**

```python
# web/census_refresh.py
"""Background refresh orchestration for census-backed lookups. The ONLY place
that triggers a Census call from the web layer. Throttled (>=15 min between
attempts per entity), deduped (one in-flight per key), and skipped entirely when
Census health is down. On success: merge into census_store, update the hot
in-memory cache, and publish an SSE record event."""

from __future__ import annotations

import asyncio
import logging
import time

from census import census_store
from census.client import CensusClient
from web import census_events, census_health
from web.cache import character_cache
from web.config import SERVICE_ID as _SERVICE_ID
from web.config import WORLD as _WORLD

_log = logging.getLogger(__name__)

_THROTTLE = 900  # 15 minutes between refresh attempts per entity
_last_attempt: dict[str, float] = {}
_in_flight: set[str] = set()


def _reset_for_test() -> None:
    _last_attempt.clear()
    _in_flight.clear()


def _should_refresh(key: str) -> bool:
    if census_health.is_down():
        return False
    if key in _in_flight:
        return False
    last = _last_attempt.get(key)
    return last is None or (time.monotonic() - last) >= _THROTTLE


def _mark_attempt(key: str) -> None:
    _last_attempt[key] = time.monotonic()


def request_character_refresh(name: str) -> None:
    """Fire-and-forget a throttled background character refresh."""
    key = f"{name.lower()}:{_WORLD.lower()}"
    if not _should_refresh(key):
        return
    _mark_attempt(key)
    _in_flight.add(key)
    asyncio.create_task(_run_character_refresh(name, key))


async def _run_character_refresh(name: str, key: str) -> None:
    from web.routes.character import _build_char_response  # local: avoid import cycle
    try:
        client = CensusClient(service_id=_SERVICE_ID)
        try:
            char = await client.get_character(name, _WORLD)
        finally:
            await client.close()
        if char is None:
            return  # not found / not resolved → keep best-known
        resp = _build_char_response(char)  # CharacterResponse (pydantic)
        data = resp.model_dump()
        resolved = bool(data.get("cls") or data.get("level"))
        conn = census_store.init_db(census_store.DB_PATH)
        try:
            census_store.upsert_character(conn, name, _WORLD, data, resolved=resolved)
        finally:
            conn.close()
        if resolved:
            character_cache.set(key, resp)
            census_events.publish({"type": "character", "key": key, "data": data,
                                   "fetched_at": int(time.time())})
    except Exception as exc:
        _log.warning("[census-refresh] character %s failed: %s", name, exc)
    finally:
        _in_flight.discard(key)
```

- [ ] **Step 4: Run — expect pass.**

- [ ] **Step 5: Commit**

```bash
git add web/census_refresh.py tests/web/test_census_refresh.py
git commit -m "feat(census): character refresh orchestrator (throttle/dedupe/health-gate)"
```

---

### Task 8: Guild refresh orchestrator (roster rebuild from best-known)

**Files:**
- Modify: `web/census_refresh.py`
- Test: `tests/web/test_census_refresh.py`

- [ ] **Step 1: Write the failing test** (pure helper that rebuilds a roster from best-known data)

```python
# tests/web/test_census_refresh.py  (append)
def test_merge_roster_keeps_best_known():
    # member resolved this time -> use fresh; member didn't -> fall back to stored
    fresh = {"Menludiir": {"name": "Menludiir", "level": 92, "cls": "Templar"}}
    roster = [{"name": "Menludiir", "rank": "Leader"}, {"name": "Alt", "rank": "Member"}]
    stored = {"alt": {"name": "Alt", "level": 80, "cls": "Fury"}}
    out = cr._merge_roster(roster, fresh, stored)
    by = {m["name"]: m for m in out}
    assert by["Menludiir"]["level"] == 92        # fresh
    assert by["Alt"]["level"] == 80              # best-known from stored
    assert by["Menludiir"]["rank"] == "Leader"   # rank from roster
```

- [ ] **Step 2: Run — expect fail.**

- [ ] **Step 3: Implement** the helper + the guild refresh entrypoint

```python
# web/census_refresh.py  (append)

def _merge_roster(roster: list[dict], fresh: dict[str, dict], stored: dict[str, dict]) -> list[dict]:
    """Build the displayed roster: each member's rank from the (reliable) roster
    list, merged with the best-known per-member data — fresh resolve if present,
    else the stored character record (keyed by lower-cased name)."""
    out: list[dict] = []
    for m in roster:
        name = m["name"]
        data = fresh.get(name) or stored.get(name.lower()) or {}
        out.append({**data, "name": name, "rank": m.get("rank"), "rank_id": m.get("rank_id")})
    return out


def request_guild_refresh(name: str) -> None:
    key = f"guild:{name.lower()}:{_WORLD.lower()}"
    if not _should_refresh(key):
        return
    _mark_attempt(key)
    _in_flight.add(key)
    asyncio.create_task(_run_guild_refresh(name, key))


async def _run_guild_refresh(name: str, key: str) -> None:
    from web.routes.guild import _persist_and_publish_guild  # local: avoid import cycle
    try:
        await _persist_and_publish_guild(name)
    except Exception as exc:
        _log.warning("[census-refresh] guild %s failed: %s", name, exc)
    finally:
        _in_flight.discard(key)
```

> Note: the heavy guild work (calling `get_guild_full`, upserting members, building + caching the roster/info/spells/adorns, publishing SSE) lives in `web/routes/guild.py` as `_persist_and_publish_guild` (Task 10), because it reuses that module's existing builders. `census_refresh` only owns throttle/dedupe/health-gate.

- [ ] **Step 4: Run the helper test — expect pass.** (The `_run_guild_refresh` path is covered in Task 10.)

- [ ] **Step 5: Commit**

```bash
git add web/census_refresh.py tests/web/test_census_refresh.py
git commit -m "feat(census): guild refresh orchestrator + best-known roster merge"
```

---

## Phase 5 — Wire the read paths to serve stored data

### Task 9: Character route — serve stored, never sync-fetch, freshness flags

**Files:**
- Modify: `web/routes/character.py` (`CharacterResponse` model; `get_character` endpoint near line 388; remove the sync-fetch fallback)
- Test: `tests/web/test_character.py`

- [ ] **Step 1: Add `fetched_at` + `stale` to `CharacterResponse`**

In `web/routes/character.py`, in the `CharacterResponse` model add:
```python
    fetched_at: int | None = None   # unix s of last resolved data (freshness)
    stale: bool = False             # served from store older than the staleness window
```

- [ ] **Step 2: Write the failing test** (Census down + stored row → serve stored, no 500)

```python
# tests/web/test_character.py  (append)
@pytest.mark.asyncio
async def test_character_served_from_store_when_census_down(app, tmp_path, monkeypatch):
    from census import census_store
    from web.routes import character as cmod

    conn = census_store.init_db(tmp_path / "census.db")
    census_store.upsert_character(conn, "Stored", "Varsoon",
                                  {"id": "1", "name": "Stored", "level": 90, "cls": "Templar", "world": "Varsoon"},
                                  resolved=True, now=1000)
    conn.close()
    monkeypatch.setattr(census_store, "DB_PATH", tmp_path / "census.db")
    # Census "down": any client construction would fail the test if used.
    monkeypatch.setattr(cmod, "CensusClient", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no Census on request path")))
    cmod.character_cache.delete("stored:varsoon")

    with patch("web.routes.character._require_user", lambda *a, **k: None) if False else _noop():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/character/Stored")
    assert r.status_code == 200
    body = r.json()
    assert body["level"] == 90
    assert body["stale"] is True
```

(If `_noop()` isn't already a helper in the file, replace the `with` line with a plain call — the character endpoint isn't auth-gated. Keep the test to: hit the endpoint, assert 200 + stored data + `stale`.)

- [ ] **Step 3: Run — expect fail** (current miss path constructs `CensusClient` synchronously).

- [ ] **Step 4: Rewrite the `get_character` miss path**

Replace the body of `get_character` (lines ~388-416) with the serve-stored model:
```python
@router.get("/character/{name}", response_model=CharacterResponse)
@limiter.limit("30/minute")
async def get_character(request: Request, name: str) -> CharacterResponse:
    """Serve last-known data instantly; refresh from Census only in the
    background. Never blocks on / fails because of Census."""
    if len(name) > 64:
        raise HTTPException(status_code=400, detail="Character name is too long")
    cache_key = f"{name.lower()}:{_WORLD.lower()}"
    now = int(__import__("time").time())
    STALE_S = 900  # 15 min

    # 1) Hot in-memory copy.
    cached, _is_stale = character_cache.get_stale(cache_key)
    if cached is not None:
        return cached

    # 2) Durable store.
    from census import census_store
    conn = census_store.init_db(census_store.DB_PATH)
    try:
        rec = census_store.get_character(conn, name, _WORLD)
    finally:
        conn.close()
    if rec is not None:
        from web.census_refresh import request_character_refresh
        stale = (now - rec["last_resolved_at"]) > STALE_S
        if stale:
            request_character_refresh(name)  # throttled/health-gated background refresh
        resp = CharacterResponse(**{**rec["data"], "fetched_at": rec["last_resolved_at"], "stale": stale})
        character_cache.set(cache_key, resp)
        return resp

    # 3) Never seen. Try one live fetch; if Census is down, return a clean
    #    "not cached" 200 rather than a 500.
    from web import census_health
    if census_health.is_down():
        raise HTTPException(status_code=503, detail=f"'{name}' not cached yet and Census is unavailable. Try again shortly.")
    client = CensusClient(service_id=_SERVICE_ID)
    try:
        char = await client.get_character(name, _WORLD)
    except Exception:
        raise HTTPException(status_code=503, detail=f"'{name}' not cached yet and Census is unavailable. Try again shortly.")
    finally:
        await client.close()
    if char is None:
        raise HTTPException(status_code=404, detail=f"Character '{name}' not found on {_WORLD}")
    resp = _build_char_response(char)
    data = resp.model_dump()
    conn = census_store.init_db(census_store.DB_PATH)
    try:
        census_store.upsert_character(conn, name, _WORLD, data, resolved=True, now=now)
    finally:
        conn.close()
    resp.fetched_at = now
    character_cache.set(cache_key, resp)
    return resp
```

> The frontend treats 503 as the "not cached / Census down" message state (Task 13). `_build_char_response` is unchanged. Delete the now-dead `_bg_refresh_character` helper (replaced by `census_refresh`) if nothing else references it; otherwise leave it.

- [ ] **Step 5: Run — expect pass.**

Run: `uv run pytest tests/web/test_character.py -q`

- [ ] **Step 6: Commit**

```bash
git add web/routes/character.py tests/web/test_character.py
git commit -m "feat(census): character route serves stored data + freshness, no sync Census"
```

---

### Task 10: Guild route — persist + serve stored, freshness flags

**Files:**
- Modify: `web/routes/guild.py` (`_fetch_and_cache_guild` near line 173; add `_persist_and_publish_guild`; `get_guild`/`get_guild_info` endpoints serve store on miss; `GuildResponse` gains freshness)
- Test: `tests/web/test_guild.py`

- [ ] **Step 1: Add `fetched_at` + `stale` to `GuildResponse`**

In `web/routes/guild.py` `GuildResponse`:
```python
    fetched_at: int | None = None
    stale: bool = False
```

- [ ] **Step 2: Add `_persist_and_publish_guild`** (wraps the existing `_fetch_and_cache_guild`)

Add to `web/routes/guild.py`:
```python
async def _persist_and_publish_guild(guild_name: str) -> None:
    """Full guild refresh: fetch + warm the in-memory caches (existing behaviour),
    then persist the roster to census_store, merge resolved members into the
    character store, and publish an SSE roster event."""
    import time as _time

    from census import census_store
    from web import census_events

    await _fetch_and_cache_guild(guild_name)  # existing: warms roster/info/spells/adorns + char cache
    now = int(_time.time())
    glower, wlower = guild_name.lower(), _WORLD.lower()
    roster = guild_cache.get_stale(f"roster:{glower}:{wlower}")[0]
    if roster is None:
        return
    blob = roster.model_dump()
    conn = census_store.init_db(census_store.DB_PATH)
    try:
        census_store.upsert_guild(conn, guild_name, _WORLD, blob, now=now)
        for m in blob.get("members", []):
            resolved = bool(m.get("cls") or m.get("level"))
            census_store.upsert_character(conn, m["name"], _WORLD, m, resolved=resolved, now=now)
    finally:
        conn.close()
    census_events.publish({"type": "guild", "key": f"{glower}:{wlower}", "data": blob, "fetched_at": now})
```

> The existing `_fetch_and_cache_guild` already builds the `GuildResponse` roster from the live member overviews; persisting that blob captures the best-known snapshot. (Best-known *merge* for non-resolving members across refreshes is handled by `census_store.upsert_character`'s keep-best-known rule + reading stored data on serve.)

- [ ] **Step 3: Write the failing test** (guild served from store when Census down)

```python
# tests/web/test_guild.py  (append; mirror the character store test)
@pytest.mark.asyncio
async def test_guild_served_from_store_when_census_down(app, tmp_path, monkeypatch):
    from census import census_store
    conn = census_store.init_db(tmp_path / "census.db")
    census_store.upsert_guild(conn, "Exordium", "Varsoon",
                              {"name": "Exordium", "world": "Varsoon",
                               "members": [{"name": "Menludiir", "rank": "Leader", "level": 90, "cls": "Templar"}]},
                              now=1000)
    conn.close()
    monkeypatch.setattr(census_store, "DB_PATH", tmp_path / "census.db")
    from web import census_health
    census_health._reset_for_test()
    monkeypatch.setattr(census_health, "is_down", lambda: True)
    from web.cache import guild_cache
    guild_cache.delete("roster:exordium:varsoon")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/guild/Exordium")
    assert r.status_code == 200
    body = r.json()
    assert body["members"][0]["name"] == "Menludiir"
    assert body["stale"] is True
```

- [ ] **Step 4: Run — expect fail.**

- [ ] **Step 5: Rewrite `get_guild` (and `get_guild_info`) miss path to serve the store**

Replace the miss handling in `get_guild` (near line 559-577) so that, after the in-memory miss, it reads `census_store.get_guild`, serves it with `fetched_at`/`stale`, and calls `request_guild_refresh(guild_name)` when stale — instead of synchronously fetching. Pattern (mirror Task 9):
```python
@router.get("/guild/{guild_name}", response_model=GuildResponse)
@limiter.limit(...)
async def get_guild(request: Request, guild_name: str) -> GuildResponse:
    cache_key = f"roster:{guild_name.lower()}:{_WORLD.lower()}"
    cached, _ = guild_cache.get_stale(cache_key)
    if cached is not None:
        return cached
    import time as _time
    from census import census_store
    from web.census_refresh import request_guild_refresh
    conn = census_store.init_db(census_store.DB_PATH)
    try:
        rec = census_store.get_guild(conn, guild_name, _WORLD)
    finally:
        conn.close()
    if rec is not None:
        stale = (int(_time.time()) - rec["last_resolved_at"]) > 900
        if stale:
            request_guild_refresh(guild_name)
        resp = GuildResponse(**{**rec["data"], "fetched_at": rec["last_resolved_at"], "stale": stale})
        guild_cache.set(cache_key, resp)
        return resp
    # never seen
    from web import census_health
    if census_health.is_down():
        raise HTTPException(status_code=503, detail=f"Guild '{guild_name}' not cached yet and Census is unavailable.")
    await _persist_and_publish_guild(guild_name)
    rec_or_cached, _ = guild_cache.get_stale(cache_key)
    if rec_or_cached is None:
        raise HTTPException(status_code=404, detail=f"Guild '{guild_name}' not found on {_WORLD}")
    return rec_or_cached
```
Apply the equivalent store-serve to `get_guild_info` (it shares the `info:` cache key + the same `_persist_and_publish_guild`). The `spell-check` / `adorn-check` endpoints already build from the warmed caches; once the roster + member data come from the store, they keep working unchanged.

- [ ] **Step 6: Run — expect pass.** `uv run pytest tests/web/test_guild.py -q`

- [ ] **Step 7: Commit**

```bash
git add web/routes/guild.py tests/web/test_guild.py
git commit -m "feat(census): guild route persists + serves stored roster with freshness"
```

---

## Phase 6 — Frontend: stream context, footer, freshness

### Task 11: `useCensusStream` context (app-level EventSource)

**Files:**
- Create: `frontend/src/hooks/useCensusStream.tsx`
- Modify: `frontend/src/App.tsx` (wrap app)

- [ ] **Step 1: Implement the context**

```tsx
// frontend/src/hooks/useCensusStream.tsx
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'

type Health = 'up' | 'down' | 'unknown'
type Listener = (data: unknown, fetchedAt: number) => void

interface CensusStream {
  health: Health
  /** Subscribe to refresh records for a given key (`<name_lower>:<world>` or `guild:<g>:<w>`). */
  subscribe: (key: string, cb: Listener) => () => void
}

const Ctx = createContext<CensusStream>({ health: 'unknown', subscribe: () => () => {} })
export const useCensusStream = () => useContext(Ctx)

export function CensusStreamProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<Health>('unknown')
  const listeners = useRef<Map<string, Set<Listener>>>(new Map())

  useEffect(() => {
    const es = new EventSource('/api/census/stream', { withCredentials: true })
    es.onmessage = e => {
      let msg: any
      try { msg = JSON.parse(e.data) } catch { return }
      if (msg.type === 'health') { setHealth(msg.status as Health); return }
      if (msg.type === 'character' || msg.type === 'guild') {
        listeners.current.get(msg.key)?.forEach(cb => cb(msg.data, msg.fetched_at))
      }
    }
    es.onerror = () => setHealth('down')  // stream dropped → assume down until next health event
    return () => es.close()
  }, [])

  const subscribe = (key: string, cb: Listener) => {
    let set = listeners.current.get(key)
    if (!set) { set = new Set(); listeners.current.set(key, set) }
    set.add(cb)
    return () => set!.delete(cb)
  }

  return <Ctx.Provider value={{ health, subscribe }}>{children}</Ctx.Provider>
}
```

- [ ] **Step 2: Wrap the app** — in `frontend/src/App.tsx`, import and wrap the top-level layout/`<Routes>` in `<CensusStreamProvider>…</CensusStreamProvider>`.

- [ ] **Step 3: Typecheck** — `cd frontend && npx tsc -b` → exit 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/hooks/useCensusStream.tsx frontend/src/App.tsx
git commit -m "feat(frontend): app-level Census SSE context"
```

---

### Task 12: Footer Census-status dot

**Files:**
- Create: `frontend/src/components/CensusStatus.tsx`
- Modify: `frontend/src/App.tsx` (add to `<footer>` near line 165)

- [ ] **Step 1: Implement**

```tsx
// frontend/src/components/CensusStatus.tsx
import { useCensusStream } from '../hooks/useCensusStream'

export function CensusStatus() {
  const { health } = useCensusStream()
  const down = health === 'down'
  return (
    <span className="inline-flex items-center gap-[0.35rem]" title={down ? 'Census unavailable — showing stored data' : 'Census online'}>
      <span
        className="inline-block w-[8px] h-[8px] rounded-full"
        style={{ background: down ? 'var(--danger)' : 'var(--success)', boxShadow: `0 0 6px ${down ? 'var(--danger)' : 'var(--success)'}` }}
      />
      Census {down ? 'offline' : 'online'}
    </span>
  )
}
```

- [ ] **Step 2: Add `<CensusStatus/>`** to the `<footer>` in `frontend/src/App.tsx`.

- [ ] **Step 3: Typecheck** — `npx tsc -b` → exit 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/CensusStatus.tsx frontend/src/App.tsx
git commit -m "feat(frontend): footer Census-status indicator"
```

---

### Task 13: Freshness badge + live-swap on character & guild pages

**Files:**
- Create: `frontend/src/components/FreshnessBadge.tsx`
- Modify: `frontend/src/pages/CharacterPage.tsx`, `frontend/src/pages/GuildPage.tsx`

- [ ] **Step 1: Implement the badge**

```tsx
// frontend/src/components/FreshnessBadge.tsx
import { useCensusStream } from '../hooks/useCensusStream'

export function FreshnessBadge({ stale }: { stale: boolean | undefined }) {
  const { health } = useCensusStream()
  if (!stale) return null
  const down = health === 'down'
  return (
    <span className="text-[0.72rem] text-text-muted italic">
      {down ? 'Census unavailable — showing stored data' : 'Updating from Census…'}
    </span>
  )
}
```

- [ ] **Step 2: Character page** — in `CharacterPage.tsx`, add `fetched_at?: number` and `stale?: boolean` to the `Character` interface; render `<FreshnessBadge stale={char.stale} />` near the header; and live-swap via the stream:
```tsx
const { subscribe } = useCensusStream()
useEffect(() => {
  if (!char) return
  const key = `${char.name.toLowerCase()}:${char.world.toLowerCase()}`
  return subscribe(key, (data) => setChar(data as Character))
}, [char?.name, char?.world, subscribe])
```

- [ ] **Step 3: Guild page** — same pattern: add `fetched_at`/`stale` to `GuildData`, render `<FreshnessBadge stale={roster.stale} />`, subscribe to `guild:<name_lower>:<world_lower>` and replace the roster on a pushed event. Also surface the **503 "not cached"** response as a friendly message instead of a generic error.

- [ ] **Step 4: Typecheck** — `npx tsc -b` → exit 0. Build — `npx vite build`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/FreshnessBadge.tsx frontend/src/pages/CharacterPage.tsx frontend/src/pages/GuildPage.tsx
git commit -m "feat(frontend): freshness badge + live SSE swap on character/guild pages"
```

---

## Phase 7 — Docs + final verification

### Task 14: CLAUDE.md + full gate

**Files:**
- Modify: `CLAUDE.md` (env-var table + key-files table)

- [ ] **Step 1: Document `CENSUS_DB_PATH`** in the env-var table:
```
| `CENSUS_DB_PATH` | Override default `data/census/census.db` (persistent character/guild lookups). Set on the Railway volume mount. |
```
Add `census/census_store.py`, `web/census_health.py`, `web/census_events.py`, `web/census_refresh.py`, `web/routes/census.py` to the key-files table with one-line descriptions.

- [ ] **Step 2: Full gate**

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
cd frontend && npx tsc -b && npx vite build
```
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: census persistence env var + module map"
```

- [ ] **Step 4: Finish the branch** — invoke `superpowers:finishing-a-development-branch`.

---

## Rollout notes (post-merge)

- Provision `CENSUS_DB_PATH` on the Railway volume (e.g. `/app/data/census/census.db`); `init_db` creates it empty on first start.
- Startup pre-warm populates common entities (claimed chars + their guilds), so the cold-cache window after a deploy is minimal.
- Behaviour only *improves*: fresh in-memory hits are unchanged; the store + SSE only matter when the cache is cold or Census is down.

---

## Self-review (against the spec)

- Persistent store + deploy-survival: Tasks 1-3 (`census_store.py`), gitignored + volume.
- Keep-best-known merge: Task 2 (`upsert_character` no-op when unresolved) + Task 8 (`_merge_roster`).
- Never block on Census: Tasks 9-10 rewrite both read paths to serve store; sync fetch only on never-seen, with 503-not-crash when down.
- Background refresh + throttle + dedupe + health-gate: Task 7-8 (`census_refresh`).
- SSE live update: Tasks 5, 6, 11, 13.
- Census health poll + endpoint + footer: Tasks 4, 6, 12.
- Freshness UI ("Updating…" / "Census unavailable"): Task 13 (`FreshnessBadge`).
- Scope = character overview + guild (AA out): no AA task. ✓
- Single-process SSE caveat: documented in `census_events.py` + spec. ✓
- Env var `CENSUS_DB_PATH`: Task 14. ✓
