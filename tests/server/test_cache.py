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
