# Rankings

Code: `backend/server/api/rankings.py` (+ `rankings.sql`). Consumers of the same
dataset: `backend/server/api/character/rankings.py` (the character page
Rankings tab), `backend/server/api/export.py` (the third-party export API) and
the parse page's per-class benchmark overlay (`benchmarks_for_boss`).

Rankings are computed on read from the parses tables. There is no separate
ranking store: one expensive step builds a per-world "kills dataset", and every
board is a cheap in-memory pass over it.

## The kills dataset

`_load_primary_boss_kills(world)` builds it:

1. **Select** every winning encounter for the world, with its player count.
   Hidden (soft-deleted) parses and uploads whose logger is not a character
   claimed by the uploading account are excluded in SQL.
2. **Classify lazily.** Combatants whose `is_player` flag is NULL (never
   classified, or reset by a curator edit — see below) are classified now, and
   the encounter's player count is re-read so the classified value decides the
   scope.
3. **Scope** by player count (`_scope_for`): 1 is solo and never ranks, 2–6 is
   `group`, 7+ is `raid`. Raid has no upper cap: ACT counts mercs, pets and
   swap-ins as players, so a 24-player raid routinely counts higher. The raid
   tuple's upper bound in `_SCOPES` is display only.
4. **Resolve the boss** (`_resolve_boss`, below) and canonicalise zone + title.
5. **Mirror-group** uploads of the same fight (`_group_into_fights`, shared with
   the parses list) and keep the primary (longest) upload.
6. **Attach combatants** for every kept fight in one query.
7. **Cut-parse gate.** ACT can end an encounter the moment any mob dies, which
   cuts multi-mob boss fights short and inflates encDPS. A kill of a curated
   encounter with more than one mob ranks only if every curated mob is present
   and died at least once. Excluded kills are logged once per encounter and
   reported to admins at `GET /api/rankings/excluded`.
8. **Era lock** (below).

## Boss resolution and curated-zone gating

The zones catalogue (curator-managed encounter rosters) is authoritative:

- A fight title matching a curated encounter mob is a boss, remapped to the
  canonical zone + encounter name. This collapses ACT zone-name variants and
  makes all mobs of a multi-mob encounter resolve to one entry.
- Inside a **curated** zone, an unmatched title never ranks. The `is_boss`
  heuristic would accept player-shaped names, so it is not consulted there.
- In an **uncurated** zone the `is_boss` heuristic applies and the ACT
  zone/title is kept verbatim, so kills in zones the curators have not reached
  still surface.

Name matching goes through `_normalise_boss_key` (NFC, lowercase, apostrophe
and space variants collapsed). The frontend mirror is `normaliseBossName` in
`RankingsPage.tsx`, and the desktop parser's raid-only filter uses the same key
shape (`GET /api/zones/raid-bosses`, built by `_raid_boss_names` from
`raid_x4`/`raid_x2` zones). Keep all three in step.

## Raid vs group trees

`_cached_zones_data()` returns two dropdown trees, keyed by **zone type**:

- **Raid tree** — zones typed `raid_x4`, with every curated boss listed even
  before anyone has killed it. Heuristic-matched raid kills in uncurated zones
  are appended under an "Other" expansion.
- **Dungeon tree** — zones with the `dungeon` overlay type (curated max-level
  group instances). Group kills in uncurated zones never reach the dropdown,
  though they remain in the database.

The trees are split by type rather than "has encounters" because curated
dungeons have bosses too and must not appear under Raids.

The filters endpoint also returns the raid expansions (newest first) and a
default expansion: the server's `current_xpac` (short code or full name) when it
has raids, otherwise the most recent expansion.

## Boards

| Metric | Scope | Board |
|---|---|---|
| `dps` / `hps` | raid, group | Per-character best encDPS/encHPS; optional class or archetype filter |
| `speed` | raid | Per-guild fastest clear |
| `speed` | group | Per-character fastest clear (dungeon groups are mixed-guild, so guild attribution is meaningless) |
| `guild_dps` | raid only | Per-guild best summed raid encDPS |

Percentiles are relative to the board leader *after* any class filter, so the
top displayed row always reads 100. Speed inverts the ratio (fastest / this).

Player rows use `_is_player_combatant` (an ally with a single-word name that is
not "Unknown"). The export API uses the same predicate so a ranked character
always appears there.

## Era lock

Once a server has rolled to a new expansion (`backend/server/xpac_rollover.py`
stamps `current_xpac_started_dt` with the *scheduled* rollover instant, not the
time the poller noticed), leaderboards for older expansions freeze: a kill in a
zone from an earlier expansion only ranks if it was ingested before that
cutoff. In-era zones, zones with no expansion info, and servers that have never
rolled over are untouched. Guild progression on the raids page reads parses
directly and is deliberately outside this filter.

## Caching and process locality

- **Kills cache** (`rankings_cache`, per world): ttl 10 minutes, max_age 6
  hours, stale-while-revalidate. A stale board is served instantly while a
  background rebuild runs; only a completely cold cache blocks. The ttl is long
  on purpose: each rebuild reads every winning kill's combatants from Postgres
  (tens of MB of Supabase egress), so a short ttl under steady traffic can
  exhaust the monthly egress allowance. Fresh uploads reach the boards within
  about ten minutes.
- **One build per world.** The startup prewarm (`prewarm_rankings_kills`),
  stale background refreshes and cold-cache requests all await one shared task
  (`_kills_build_task`). `asyncio.shield` stops a navigation-aborted request
  from cancelling the build others are waiting on. `_rebuild_kills` writes the
  cache without reading it first, so the stale entry stays servable during the
  rebuild.
- **Filters never block on the scan** unless there is no curated catalogue at
  all (dev/tests), because the dropdowns' authoritative content is the
  catalogue, not the kills.
- **Benchmarks are cache-only** (`rankings_cache.peek`): the parse page must
  never trigger a rebuild inline; an empty overlay is the cold-cache behaviour.
- **Zone catalogue caches** (`_cached_zones_data`, `_zone_canonical_map`,
  `_encounter_required_mobs`, `_raid_boss_names`, the era-lock zone map) are
  per-process LRUs cleared by `invalidate_zones_cache()`. That only clears the
  worker that handled the curator edit, which is safe because a startup check
  in `backend/server/app.py` pins `WEB_CONCURRENCY=1`. Running more workers
  would need a shared invalidation channel or short TTLs.

`invalidate_zones_cache(zone_name, reclassify=...)` must be called after any
change to zones or encounter rosters. It clears the caches above and the parses
classifier map, and (when `reclassify`) resets `is_player` on that zone's
combatants so they reclassify on next read. The reset is a real UPDATE over
indexed rows, so curator edits pass their zone and run it via `run_sync`; edits
that cannot change classification (reorders, featured-raid curation) pass
`reclassify=False`.

## Worlds and threads

Each request resolves `current_world()` in the async handler and passes the
world down explicitly. Do not read `current_world()` inside executor threads
unless they were started with `run_sync` (which copies contextvars).
