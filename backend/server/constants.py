"""Named constants for magic numbers scattered across the backend.

Owns: cache TTLs, refresh throttles, mirror/dedup windows, request-list caps,
SQLite parameter-chunk safety limit. Each constant carries a comment naming
the code path it gates so a future contributor can search by intent rather
than by literal value.

Adding a new constant: append here, then `from web.constants import FOO`
at the consumer site. Never re-declare a constant for "local" use — the
audit found three independent `_THROTTLE = 900` / `STALE_S = 900` / `> 900`
literals for the same concept; this module exists to make that mistake
visible.
"""

from __future__ import annotations

# --- Cache TTLs ------------------------------------------------------------

# stale-while-revalidate window for character/guild/aa caches.
# Below this age → return directly; above → return + fire background refresh.
CACHE_STALE_TTL_S: int = 300  # 5 min

# Hard-expiry window — entries older than this are evicted and the next
# request MUST do a sync fetch. Bounds memory growth for never-revisited
# keys (see web/cache.TTLCache.sweep).
CACHE_MAX_AGE_S: int = 3600  # 1 hr


# --- Census refresh orchestration -----------------------------------------

# Per-entity throttle: subsequent refresh attempts before this elapses are
# silently dropped. Stops a hot-cache miss from triggering hundreds of
# in-flight Census calls for the same character. Same value as the
# character-row staleness window so a stale row triggers exactly one refresh.
CENSUS_REFRESH_THROTTLE_S: int = 900  # 15 min

# A character record in census_store is "stale" once last_resolved_at is
# older than this. Surfaced on CharacterResponse.stale so the frontend can
# render a small "may be outdated" badge.
CHARACTER_STALE_S: int = 900  # 15 min


# --- Parses listing + mirroring -------------------------------------------

# Mirror grouping: two uploads are the same fight when their (guild, title)
# match and their start times fall within this window. Faithful to the
# pre-server-side ParsesPage detectMirrors rule.
PARSE_MIRROR_WINDOW_S: int = 60

# Maximum FIGHT cap on /api/parses?limit=... — protects the browser from
# stalling on a multi-thousand-row render rather than the server. The
# inner SQL cap is `limit * PARSE_INNER_CAP_MULTIPLIER` (see below).
PARSE_LIST_MAX_LIMIT: int = 500

# Inner SQL cap multiplier — worst-case 24 mirror uploads per fight, so a
# 500-fight request needs 12_000 raw upload rows; round up to 15_000 for
# headroom. Floor of 2000 covers very small page requests.
PARSE_INNER_CAP_MULTIPLIER: int = 30
PARSE_INNER_CAP_FLOOR: int = 2000

# Admin parses listing cap — looser than the public one (an admin reviewing
# uploads needs a wider view than a casual reader).
ADMIN_PARSE_LIST_MAX_LIMIT: int = 1000


# --- SQLite ---------------------------------------------------------------

# SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999; chunked lookups need
# to stay under this. 900 leaves headroom for the surrounding fixed params
# in the same query.
SQLITE_VAR_CHUNK_SAFE: int = 900


# --- API tokens -----------------------------------------------------------

# Per-token last_used_at coalescing window (BE-011). UPDATE only fires if
# the existing value is older than this — sub-minute precision isn't
# useful to the UI and the write storm during a raid was a real cost.
API_TOKEN_LAST_USED_COALESCE_S: int = 60


# --- Background tasks -----------------------------------------------------

# Cache-sweep loop interval (see web/app.py:_cache_sweep_loop).
CACHE_SWEEP_INTERVAL_S: int = 600  # 10 min

# Parses retention-sweep loop interval (see app.py:_parse_cleanup_loop).
# 6 h is plenty — the cutoff is days, so cadence only bounds how stale a
# just-past-cutoff parse can be before it's swept.
PARSE_CLEANUP_INTERVAL_S: int = 6 * 60 * 60  # 6 h
