"""
Simple in-memory TTL cache shared across all requests.
Safe for single-process asyncio (no locking needed).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from web.constants import CACHE_MAX_AGE_S, CACHE_STALE_TTL_S
from web.lib.silent_swallow import swallow

_log = logging.getLogger(__name__)

CacheName = Literal["character", "guild", "claim", "aa", "rankings"]


class TTLCache:
    def __init__(
        self,
        ttl: int = CACHE_STALE_TTL_S,
        max_age: int | None = CACHE_MAX_AGE_S,
        name: CacheName = "default",  # type: ignore[assignment]  # "default" is fallback only
        maxsize: int = 1000,
    ):
        """
        ttl:     seconds until data is considered stale → background refresh fires,
                 but the cached value is still returned immediately.
        max_age: seconds until data is hard-expired → evicted and caller must fetch
                 synchronously (user waits).  None = never hard-expire.
        name:    label used in Prometheus metrics (character | guild | claim | …).
        maxsize: maximum number of entries.  When full, the oldest entry is evicted
                 before inserting a new one (LRU-by-insertion-order).
        """
        self._ttl = ttl
        self._max_age = max_age
        self._name = name
        self._maxsize = maxsize
        self._store: dict[str, tuple[float, Any]] = {}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _inc_hit(self) -> None:
        with swallow("metrics"):
            from web.metrics import CACHE_HITS

            CACHE_HITS.labels(cache=self._name).inc()

    def _inc_miss(self) -> None:
        with swallow("metrics"):
            from web.metrics import CACHE_MISSES

            CACHE_MISSES.labels(cache=self._name).inc()

    def _inc_stale(self) -> None:
        with swallow("metrics"):
            from web.metrics import CACHE_STALE

            CACHE_STALE.labels(cache=self._name).inc()

    def _update_size(self) -> None:
        with swallow("metrics"):
            from web.metrics import CACHE_SIZE

            CACHE_SIZE.labels(cache=self._name).set(len(self._store))

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str) -> Any | None:
        """Return value if within TTL, else None (and evict)."""
        entry = self._store.get(key)
        if entry is None:
            self._inc_miss()
            return None
        ts, value = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            self._update_size()
            self._inc_miss()
            return None
        _log.debug("[Cache] HIT   %s", key)
        self._inc_hit()
        return value

    def get_stale(self, key: str) -> tuple[Any | None, bool]:
        """
        Stale-while-revalidate lookup with optional hard expiry.

        Returns (value, is_stale):
          - age <= ttl              → (value, False)   fresh, return as-is
          - ttl < age <= max_age   → (value, True)    stale, caller should fire background refresh
          - age > max_age          → (None,  False)   hard-expired, evicted; caller must fetch sync
          - not in cache           → (None,  False)   cache miss
        """
        entry = self._store.get(key)
        if entry is None:
            self._inc_miss()
            return None, False
        ts, value = entry
        age = time.monotonic() - ts
        if self._max_age is not None and age > self._max_age:
            del self._store[key]
            self._update_size()
            _log.debug("[Cache] EXPIRED %s (%.1f min old)", key, age / 60)
            self._inc_miss()
            return None, False
        is_stale = age > self._ttl
        _log.debug("[Cache] %s %s", "STALE" if is_stale else "HIT  ", key)
        if is_stale:
            self._inc_stale()
        else:
            self._inc_hit()
        return value, is_stale

    def set(self, key: str, value: Any) -> None:
        _log.debug("[Cache] SET   %s", key)
        # Evict oldest entry if we're at capacity and this is a new key.
        if key not in self._store and len(self._store) >= self._maxsize:
            oldest_key = next(iter(self._store))
            del self._store[oldest_key]
            _log.debug("[Cache] EVICT (maxsize) %s", oldest_key)
        self._store[key] = (time.monotonic(), value)
        with swallow("metrics"):
            from web.metrics import CACHE_SETS

            CACHE_SETS.labels(cache=self._name).inc()
        self._update_size()

    def sweep(self) -> int:
        """
        Proactively evict all entries that have exceeded max_age.
        Call periodically (e.g. on a background task) to prevent the store from
        holding stale entries for keys that are never accessed again.
        Returns the number of entries removed.
        """
        if self._max_age is None:
            return 0
        now = time.monotonic()
        expired = [k for k, (ts, _) in self._store.items() if now - ts > self._max_age]
        for k in expired:
            del self._store[k]
        if expired:
            _log.debug("[Cache] SWEEP removed %d expired entries from %s", len(expired), self._name)
            self._update_size()
        return len(expired)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
        _log.debug("[Cache] DEL   %s", key)
        self._update_size()


# One instance per domain.
# ttl=300     → stale after 5 min (background refresh fires, response still instant)
# max_age=3600 → hard-expired after 1 hr (user waits for a fresh fetch)
# maxsize     → LRU-by-insertion eviction when full, prevents unbounded growth
#
# Sizing rationale:
#   character: one entry per character name; 500 covers a large active guild + visitors
#   guild:     ~5 cache keys per guild (roster/info/spells/adorns/chars); 50 covers 10 guilds
#   claim:     one entry per discord_id; 200 covers a large player base
#   aa:        one entry per character; 200 covers regular users
character_cache: TTLCache = TTLCache(name="character", maxsize=500)
guild_cache: TTLCache = TTLCache(name="guild", maxsize=50)
claim_cache: TTLCache = TTLCache(name="claim", maxsize=200)
aa_cache: TTLCache = TTLCache(name="aa", maxsize=200)
