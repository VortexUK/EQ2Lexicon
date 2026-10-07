# Census client lifecycle and health

How the web app talks to the Daybreak Census API: one shared HTTP client per
event loop, and a site-wide availability signal that decides whether a live
Census call is worth attempting at all.

## One shared client per event loop

`backend/server/core/census_lifecycle.py` owns the web layer's `CensusClient`.
Every caller borrows it instead of constructing its own:

```python
from backend.server.core.census_lifecycle import shared_census_client

async with shared_census_client() as c:
    char = await c.get_character(name, world)
```

`get_shared_census_client()` returns the same object without a context
manager. In both cases **the caller must never close the client**: the module
owns it.

Why a shared client: each `CensusClient` wraps an `aiohttp.ClientSession`.
A session per call means a new connection pool and a fresh TLS handshake on
every Census request. aiohttp is designed around one long-lived session.

Why it is keyed by event loop (`id(loop)`): an `aiohttp.ClientSession` is
bound to the loop it was created on and raises `RuntimeError` if used after
that loop closes. pytest-asyncio creates a fresh loop per test, so each test
loop gets its own client. In production `main.py` runs the web server and the
Discord bot on one loop that lives for the whole process, so there is exactly
one shared client.

The bot builds its own `CensusClient` in `backend/bot/bot.py` and is not
affected by this module.

Shutdown: the FastAPI lifespan in `backend/server/app.py` calls
`census_lifecycle.aclose_all()`, which closes every per-loop client so the
process exits without aiohttp's "Unclosed client session" warning. It is
safe to call more than once. Tests that swap in a mock client call
`_reset_for_test()`, which clears the map without closing anything.

## Census availability: `census_health`

`backend/server/census_health.py` is the site-wide "is Census up?" signal.
The refresh paths (`census_refresh`, `refresh_queue`, `recruitment_sweep`)
and the character, guild, claim, AA and gear-set routes consult
`census_health.is_down()` before attempting a live call, so a Census outage
degrades to "serve the stored copy" instead of piling up timeouts.

`is_down()` is true when either of two independent signals says so:

1. **The probe.** `poll_loop()` (started from the app lifespan) calls
   `refresh_health()` immediately and then every 5 minutes. It fetches
   `world?c:limit=1` (a tiny real collection, not the base index) and only
   counts the response as healthy if it is HTTP 200, parses as JSON, has no
   top-level `errorCode`, and has a non-negative `returned` count. Census
   can answer `200 OK` with `{"errorCode":"SERVER_ERROR"}` during outages,
   so the status code alone is not enough.
2. **The breaker** (`backend/census/failures.py`). The transport wrapper in
   `backend/census/client.py` records every real request failure. Three
   failures inside 60 seconds trip the breaker; any healthy response closes
   it immediately. This catches the common case where the probe's tiny
   `world` query stays green while `character/` queries are timing out. It
   matters because the typed getters return `None` for both "failed" and
   "not found": without the breaker a brown-out would surface to users as
   false 404s.

All state is in process memory, which matches the single-process deployment.

### Server status feed

On a successful probe the same loop also fetches Daybreak's cross-game
`game_server_status` feed, keeps the EQ2 rows, and stores
`{world_lower: {name, state, reported_at}}`. `get_server_state(world)` reads
it, and the `/census/server-status` route (`backend/server/api/census.py`) serves it for the footer
indicator. The feed is skipped while Census is down, and a
failed fetch keeps the previous map, since a stale state is more useful than
an empty one.

### Publishing changes

When the probe's verdict changes, `refresh_health()` publishes a
`{"type": "health", ...}` event through `census_events`, which the SSE
stream (the `/census/stream` route) forwards to open browsers.
The `/census/health` route returns `get_state()` for first paint.
`census_health_status` (1 = up) is exported as a Prometheus gauge at scrape
time.
