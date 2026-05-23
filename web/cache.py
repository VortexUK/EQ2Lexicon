"""
Simple in-memory TTL cache shared across all requests.
Safe for single-process asyncio (no locking needed).
"""
from __future__ import annotations

import time
from typing import Any, Optional


class TTLCache:
    def __init__(self, ttl: int = 300, max_age: int | None = None, name: str = "default"):
        """
        ttl:     seconds until data is considered stale → background refresh fires,
                 but the cached value is still returned immediately.
        max_age: seconds until data is hard-expired → evicted and caller must fetch
                 synchronously (user waits).  None = never hard-expire.
        name:    label used in Prometheus metrics (character | guild | claim | …).
        """
        self._ttl = ttl
        self._max_age = max_age
        self._name = name
        self._store: dict[str, tuple[float, Any]] = {}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _inc_hit(self) -> None:
        try:
            from web.metrics import CACHE_HITS
            CACHE_HITS.labels(cache=self._name).inc()
        except Exception:
            pass

    def _inc_miss(self) -> None:
        try:
            from web.metrics import CACHE_MISSES
            CACHE_MISSES.labels(cache=self._name).inc()
        except Exception:
            pass

    def _inc_stale(self) -> None:
        try:
            from web.metrics import CACHE_STALE
            CACHE_STALE.labels(cache=self._name).inc()
        except Exception:
            pass

    def _update_size(self) -> None:
        try:
            from web.metrics import CACHE_SIZE
            CACHE_SIZE.labels(cache=self._name).set(len(self._store))
        except Exception:
            pass

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[Any]:
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
        print(f"[Cache] HIT   {key}")
        self._inc_hit()
        return value

    def get_stale(self, key: str) -> tuple[Optional[Any], bool]:
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
            print(f"[Cache] EXPIRED {key} ({age / 60:.1f} min old)")
            self._inc_miss()
            return None, False
        is_stale = age > self._ttl
        print(f"[Cache] {'STALE' if is_stale else 'HIT  '} {key}")
        if is_stale:
            self._inc_stale()
        else:
            self._inc_hit()
        return value, is_stale

    def set(self, key: str, value: Any) -> None:
        print(f"[Cache] SET   {key}")
        self._store[key] = (time.monotonic(), value)
        try:
            from web.metrics import CACHE_SETS
            CACHE_SETS.labels(cache=self._name).inc()
        except Exception:
            pass
        self._update_size()

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
        print(f"[Cache] DEL   {key}")
        self._update_size()


# One instance per domain.
# ttl=300  → stale after 5 min (background refresh fires, response still instant)
# max_age=3600 → hard-expired after 1 hr (user waits for a fresh fetch)
character_cache: TTLCache = TTLCache(ttl=300, max_age=3600, name="character")
guild_cache:     TTLCache = TTLCache(ttl=300, max_age=3600, name="guild")
claim_cache:     TTLCache = TTLCache(ttl=300, max_age=3600, name="claim")
