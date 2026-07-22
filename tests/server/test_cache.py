"""Tests for web.cache.TTLCache."""

from __future__ import annotations

import time

import pytest

from backend.server.cache import TTLCache


class TestTTLCacheFreshHit:
    def test_fresh_hit_returns_value(self):
        cache = TTLCache(ttl=300, max_age=3600)
        cache.set("key", "value")
        val, stale = cache.get_stale("key")
        assert val == "value"
        assert stale is False

    def test_get_returns_value_within_ttl(self):
        cache = TTLCache(ttl=300, max_age=3600)
        cache.set("mykey", 42)
        assert cache.get("mykey") == 42


class TestTTLCacheStaleness:
    def test_stale_after_ttl(self):
        cache = TTLCache(ttl=1, max_age=3600)
        # Inject an entry with a timestamp 2 seconds in the past
        cache._store["key"] = (time.monotonic() - 2, "stale_value")
        val, stale = cache.get_stale("key")
        assert val == "stale_value"
        assert stale is True

    def test_get_returns_none_after_ttl(self):
        cache = TTLCache(ttl=1, max_age=3600)
        # Inject old entry (past TTL)
        cache._store["key"] = (time.monotonic() - 2, "old_value")
        result = cache.get("key")
        assert result is None

    def test_get_evicts_stale_entry(self):
        cache = TTLCache(ttl=1, max_age=3600)
        cache._store["key"] = (time.monotonic() - 2, "old_value")
        cache.get("key")
        assert "key" not in cache._store


class TestTTLCacheHardExpiry:
    def test_hard_expired_returns_none(self):
        cache = TTLCache(ttl=1, max_age=2)
        # Inject entry 3 seconds old — beyond max_age=2
        cache._store["key"] = (time.monotonic() - 3, "expired_value")
        val, stale = cache.get_stale("key")
        assert val is None
        assert stale is False

    def test_hard_expired_evicts_entry(self):
        cache = TTLCache(ttl=1, max_age=2)
        cache._store["key"] = (time.monotonic() - 3, "expired_value")
        cache.get_stale("key")
        assert "key" not in cache._store

    def test_no_max_age_never_hard_expires(self):
        cache = TTLCache(ttl=1, max_age=None)
        # Inject very old entry — without max_age it should still return stale
        cache._store["key"] = (time.monotonic() - 100000, "ancient_value")
        val, stale = cache.get_stale("key")
        assert val == "ancient_value"
        assert stale is True


class TestTTLCacheMiss:
    def test_miss_returns_none_false(self):
        cache = TTLCache(ttl=300, max_age=3600)
        val, stale = cache.get_stale("nonexistent")
        assert val is None
        assert stale is False

    def test_get_returns_none_on_miss(self):
        cache = TTLCache(ttl=300)
        assert cache.get("nonexistent") is None


class TestTTLCacheSetDelete:
    def test_set_and_delete(self):
        cache = TTLCache(ttl=300, max_age=3600)
        cache.set("k", "v")
        cache.delete("k")
        val, stale = cache.get_stale("k")
        assert val is None
        assert stale is False

    def test_delete_nonexistent_does_not_raise(self):
        cache = TTLCache(ttl=300)
        cache.delete("no_such_key")  # should not raise

    def test_overwrite_refreshes_timestamp(self):
        cache = TTLCache(ttl=1, max_age=3600)
        # Inject an old entry
        cache._store["k"] = (time.monotonic() - 2, "old")
        # Overwrite with a fresh set
        cache.set("k", "new")
        val, stale = cache.get_stale("k")
        assert val == "new"
        assert stale is False

    def test_multiple_keys_independent(self):
        cache = TTLCache(ttl=300, max_age=3600)
        cache.set("a", 1)
        cache.set("b", 2)
        a_val, _ = cache.get_stale("a")
        b_val, _ = cache.get_stale("b")
        assert a_val == 1
        assert b_val == 2
        cache.delete("a")
        assert cache.get("a") is None
        assert cache.get("b") == 2


class TestLRUEviction:
    """maxsize eviction is LRU-by-access (2026-07): reads and overwrites move
    an entry to the back of the eviction queue, so hot entries survive
    roster-flood inserts."""

    def test_read_saves_an_entry_from_eviction(self):
        cache = TTLCache(ttl=300, max_age=3600, maxsize=3)
        for k in ("a", "b", "c"):
            cache.set(k, k)
        cache.get_stale("a")  # touch the oldest
        cache.set("d", "d")  # over capacity → evicts the LRU entry
        assert cache.get_stale("a")[0] == "a"  # touched — survived
        assert cache.get_stale("b")[0] is None  # untouched oldest — evicted

    def test_get_also_touches(self):
        cache = TTLCache(ttl=300, max_age=3600, maxsize=3)
        for k in ("a", "b", "c"):
            cache.set(k, k)
        cache.get("a")
        cache.set("d", "d")
        assert cache.get("a") == "a"
        assert cache.get("b") is None

    def test_overwrite_moves_to_back_of_queue(self):
        cache = TTLCache(ttl=300, max_age=3600, maxsize=3)
        for k in ("a", "b", "c"):
            cache.set(k, k)
        cache.set("a", "a2")  # overwrite — must refresh queue position
        cache.set("d", "d")
        assert cache.get_stale("a")[0] == "a2"
        assert cache.get_stale("b")[0] is None


class TestPeek:
    """peek() is the opportunistic-probe read: no metrics, no LRU touch."""

    def test_peek_returns_fresh_and_stale_values(self):
        cache = TTLCache(ttl=1, max_age=3600)
        cache.set("fresh", 1)
        cache._store["stale"] = (time.monotonic() - 2, 2)
        assert cache.peek("fresh") == 1
        assert cache.peek("stale") == 2  # stale-but-within-max-age still served
        assert cache.peek("absent") is None

    def test_peek_respects_hard_expiry(self):
        cache = TTLCache(ttl=1, max_age=10)
        cache._store["old"] = (time.monotonic() - 11, "gone")
        assert cache.peek("old") is None
        assert "old" in cache._store  # read-only: no eviction side effect

    def test_peek_records_no_metrics(self):
        from backend.server.metrics import CACHE_HITS, CACHE_MISSES

        cache = TTLCache(ttl=300, max_age=3600, name="claim")
        cache.set("k", 1)
        hits_before = CACHE_HITS.labels(cache="claim")._value.get()
        misses_before = CACHE_MISSES.labels(cache="claim")._value.get()
        cache.peek("k")
        cache.peek("nope")
        assert CACHE_HITS.labels(cache="claim")._value.get() == hits_before
        assert CACHE_MISSES.labels(cache="claim")._value.get() == misses_before

    def test_peek_does_not_touch_lru_order(self):
        cache = TTLCache(ttl=300, max_age=3600, maxsize=3)
        for k in ("a", "b", "c"):
            cache.set(k, k)
        cache.peek("a")  # must NOT save it from eviction
        cache.set("d", "d")
        assert cache.peek("a") is None
        assert cache.peek("b") == "b"


class TestStoreHitMetric:
    def test_record_store_hit_increments_counter(self):
        from backend.server.metrics import CACHE_STORE_HITS

        cache = TTLCache(ttl=300, max_age=3600, name="character")
        before = CACHE_STORE_HITS.labels(cache="character")._value.get()
        cache.record_store_hit()
        assert CACHE_STORE_HITS.labels(cache="character")._value.get() == before + 1
