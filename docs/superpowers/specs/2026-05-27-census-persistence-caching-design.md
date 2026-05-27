# Census Persistence & Resilient Caching

**Date:** 2026-05-27
**Status:** Design approved, pending implementation plan

## Problem

Guild and character lookups are cached **only in memory** (`web/cache.py`
`TTLCache`: 5-min stale → background refresh; 1-hr hard-expire → **synchronous**
Census fetch where the user waits). Two failure modes:

1. **Census down + cold/expired cache** → the request path does a synchronous
   `client.get_character` / guild fetch that throws → the page 500s
   ("service unreachable"). Census being completely down currently breaks
   guild/character pages outright.
2. **Deploy** → the entire in-memory cache is lost, so the first request to any
   entity hits Census synchronously (same failure if Census is down).

There is no persistent character/guild store today (only `users.db` claims and
the frozen combatant snapshots in `parses.db`).

## Goal

Make Census strictly a **background** refresh source. The request path must
**never block on or fail because of Census**. Always serve the last-known stored
data instantly, show its freshness, refresh in the background when stale, and
update the page live when fresh data arrives. Surface a site-wide Census-health
signal so the user understands when they're seeing stored data.

## Approved decisions

| Decision | Choice |
|---|---|
| Merge rule on refresh | **Keep best-known** — only overwrite a stored field when Census returns real data; never null-out good data (the recent-login problem). Each record carries its own `last_resolved_at`. |
| Live update | **SSE** — a completed background refresh pushes the fresh record over an event stream; the open page swaps it in with no reload. |
| Scope | **Character overview + guild** (covers the character page, the Spells tab via local `spells.db`, the guild roster, and Spell-Check / Adorn-Check which derive from member data). **AA tab excluded** for now (a separate `get_character_aas` call — clean follow-on). |
| Staleness window | **15 minutes** — older than this → show "updating…" + background refresh. Also the minimum gap between refreshes of the same entity. |
| Store model | **Normalized** `characters` + `guilds` tables (a guild resolve and a direct character visit feed the same per-character record; no duplication). |

## Architecture — three layers, request path never blocks

1. **In-memory `TTLCache`** (hot path) — a fresh hit serves instantly from RAM.
   The structure is reused, but a miss/expiry **no longer triggers a synchronous
   Census fetch** (that's the bug being removed); it falls through to `census.db`.
2. **Persistent `census.db`** (NEW, on the Railway volume) — the durable
   fallback; always holds the last-known record, survives deploys.
3. **Census** — hit **only from background tasks**, never on the request path.

Staleness (the refresh trigger + the "updating" badge) is governed by the
record's `last_resolved_at` age (15 min), not the in-memory TTL; the in-memory
TTL only gates how often we re-read `census.db`.

**Read flow** for a character/guild request:

- In-memory fresh hit → serve.
- Else read `census.db`:
  - Row exists → serve it **immediately**. If `last_resolved_at` is older than
    15 min, fire a background refresh (subject to throttle + health, below).
  - No row (never looked up) → the one unavoidable gap. Attempt a single
    on-request fetch with a short timeout; if Census is down, return a clean
    `200` "not cached yet — Census unavailable, try again shortly" state (a
    structured response the frontend renders as a message, **not** a 500).
    Mitigated by the existing startup pre-warm (claimed characters + their
    guilds), so common entities always have a row.

`census.db` is the source of truth; the in-memory cache is a fast RAM copy of it.

## Persistent store — `census.db`

New module `census/census_store.py` (mirrors `parses/db.py`: `DB_PATH` with
`CENSUS_DB_PATH` env override, `_CREATE_*` SQL, `_MIGRATIONS`, `init_db()` with
WAL, query/upsert helpers). Lives at `data/census/census.db`, gitignored,
provisioned on the Railway volume (env `CENSUS_DB_PATH`, e.g.
`/app/data/census/census.db`).

```sql
CREATE TABLE IF NOT EXISTS characters (
    name_lower       TEXT NOT NULL,
    world            TEXT NOT NULL,
    name             TEXT NOT NULL,        -- display name
    level            INTEGER,              -- queryable mirror
    guild_name       TEXT,                 -- queryable mirror
    data_json        TEXT NOT NULL,        -- the CharacterResponse
    last_resolved_at INTEGER NOT NULL,     -- unix s, last time Census returned REAL data
    updated_at       INTEGER NOT NULL,
    PRIMARY KEY (name_lower, world)
);

CREATE TABLE IF NOT EXISTS guilds (
    name_lower       TEXT NOT NULL,
    world            TEXT NOT NULL,
    name             TEXT NOT NULL,
    data_json        TEXT NOT NULL,        -- roster (member names + ranks) + guild info
    last_resolved_at INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    PRIMARY KEY (name_lower, world)
);
```

Keyed by `(name_lower, world)` — the web is single-world (`EQ2_WORLD`) but the
key is world-scoped for correctness.

## Refresh + merge (background only)

**Character refresh** (`name`, `world`):
- `get_character`. If it **resolved real data** (has class/level — the
  recent-login signal), `data_json` ← new response, `last_resolved_at` ← now,
  update in-memory + push SSE. If it returned nothing/sparse, **leave the row
  untouched** (keep best-known).

**Guild refresh** (`name`, `world`):
- `get_guild_full`. The member **list (names + ranks) is reliable** regardless of
  login recency; per-member type/gear is partial.
- For each member that resolved real data → merge-upsert into `characters`.
- Rebuild the guild `data_json`: the roster = member names + ranks joined with
  **best-known per-member data pulled from `characters`** (so a member who didn't
  resolve this time still shows their last-good level/class/gear, not blanks).
  Store + `last_resolved_at` ← now + in-memory + push SSE.
- Guild **Spell-Check** and **Adorn-Check** recompute from members' stored data
  in `characters` (so they survive Census-down too).

**Throttle + dedupe:** a refresh for an entity is skipped if it was resolved
< 15 min ago, if one is already in flight (an in-process in-flight key set, like
today's guild prewarm guard), or if Census health is **down**.

## SSE — live updates

`GET /api/census/stream` — Server-Sent Events, backed by an **in-process async
pub/sub** (a set of `asyncio.Queue` subscribers; the app is single-process
asyncio). Events:

```
{ "type": "character" | "guild", "key": "<name_lower>:<world>", "data": <record>, "fetched_at": <unix> }
{ "type": "health", "status": "up" | "down", "checked_at": <unix> }
```

A completed background refresh and a health change publish to all subscribers.
Frontend: a single app-level `EventSource` in a React context/hook. The open
character/guild page subscribes and, on a matching `key`, swaps in `data` and
clears its "updating" badge; the footer subscribes to `health`. ~20 s keep-alive
comment ping to survive proxy idle timeouts.

**Pitfall (documented, not solved here):** the in-process pub/sub only works with
a **single app process**. If the deployment ever runs multiple uvicorn workers,
a refresh in one worker won't notify clients connected to another — that would
need an external broker (e.g. Redis). Current deployment is single-process.

## Census health

A background task polls `https://census.daybreakgames.com/s:{service_id}/json/get/eq2/`
every **5 minutes**. Non-200, timeout, or exception → `status = "down"`, else
`"up"`. Status + `checked_at` held in memory (not persisted — it's live state).

- Pushed over SSE on change; `GET /api/census/health` returns the current value
  for first paint.
- **Footer indicator:** a small dot — green ("Census online") / red ("Census
  unavailable — showing stored data"), with the last-checked time in a tooltip.
- When a stale entity *would* refresh but health is down, the refresh is skipped
  and the page shows the "Census unavailable — showing stored data" state instead
  of "updating…".

## Freshness UI

Character/guild API responses gain `fetched_at` (the record's `last_resolved_at`)
and `stale` (bool, age > 15 min). The page shows an unobtrusive "as of
<relative time>" plus one of:

- **"Updating from Census…"** — stale and a refresh is in flight (Census up).
- **"Census unavailable — showing stored data"** — stale and health is down.
- (nothing) — fresh.

SSE delivery swaps the data and updates the timestamp/badge live.

## Edge cases

| Situation | Behaviour |
|---|---|
| Census down, row exists | Serve stored instantly; badge "showing stored data". No 500. |
| Census down, never-seen entity | Structured "not cached yet — Census unavailable" message (200), not a crash. |
| Deploy | `census.db` persists; in-memory cold but DB warm → first request serves stored. |
| Concurrent requests for same entity | One in-flight refresh (dedupe key); others just serve stored. |
| Guild member didn't resolve this time | Keeps last-good data from `characters`; roster name+rank still current. |

## Out of scope

- **AA tab** persistence (separate `get_character_aas`) — clean follow-on using
  the same pattern.
- **Multi-worker** SSE fan-out (would need a broker).
- Changing the existing claim / item-watch flows.

## Environment

| Variable | Description |
|---|---|
| `CENSUS_DB_PATH` | Override default `data/census/census.db`; set on the Railway volume mount. |

## Testing

- **Merge:** a sparse/empty fetch never null-outs a stored character; a resolving
  fetch updates it + bumps `last_resolved_at`.
- **Guild rebuild:** roster names/ranks refresh; a non-resolving member retains
  best-known data from `characters`.
- **Throttle/dedupe:** no refresh when < 15 min old, when one's in flight, or when
  health is down.
- **Read path:** serve-stored when Census down (no exception escapes to the
  request); never-seen + down → the structured message, not a 500.
- **Health:** non-200/timeout/exception → down; 200 → up.
- **SSE:** publish reaches subscribers; health + record events well-formed.
- Frontend: `tsc`; the freshness badge + footer states render from the flags.

## Rollout

Additive. New `census.db` is created on `init_db` (empty), pre-warm populates
common entities, and the in-memory path is unchanged for fresh hits — so behaviour
only *improves* when the cache is cold or Census is down. Provision the volume +
`CENSUS_DB_PATH` on Railway (same as the other DBs); no data migration.
