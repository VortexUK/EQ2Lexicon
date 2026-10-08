"""Tests for the metrics cardinality guards.

The failure mode being guarded: prometheus_client never forgets a label combo,
so any unbounded label value (raw URL paths from bot probes, garbage HTTP
verbs, per-user labels) mints permanent series until the process restarts.
"""

from __future__ import annotations

from prometheus_client import REGISTRY, generate_latest

from backend.server import metrics


def test_normalize_http_labels_keeps_route_templates():
    assert metrics.normalize_http_labels("GET", "/api/character/{name}") == ("GET", "/api/character/{name}")
    assert metrics.normalize_http_labels("get", "/api/server") == ("GET", "/api/server")


def test_normalize_http_labels_collapses_unmatched_and_garbage_verbs():
    # No matched route (bot probe) → single bucket, never the raw path.
    assert metrics.normalize_http_labels("POST", None) == ("POST", metrics.UNMATCHED_PATH)
    assert metrics.normalize_http_labels("POST", "") == ("POST", metrics.UNMATCHED_PATH)
    # Scanner verbs (PROPFIND, TRACK, …) → OTHER.
    assert metrics.normalize_http_labels("PROPFIND", "/{full_path:path}") == ("OTHER", "/{full_path:path}")


def test_duration_histogram_has_no_method_label():
    """Each label combo on the histogram costs ~(buckets + 2) series and no
    dashboard queries latency by method — keep it path-only."""
    assert metrics.HTTP_REQUEST_DURATION._labelnames == ("path",)


def test_created_series_disabled():
    """The OpenMetrics *_created companions double every counter/histogram's
    series count and nothing reads them — metrics.py disables them at import."""
    assert b"_created" not in generate_latest(REGISTRY)


def test_should_track_path_skips_static_mounts():
    for skipped in ("/assets/app.js", "/class-icons/13.png", "/spell-icons/1.png", "/metrics"):
        assert not metrics.should_track_path(skipped)
    assert metrics.should_track_path("/api/character/Foo")


def _active_counts() -> dict[str, float]:
    (family,) = metrics._ActiveUsersCollector().collect()
    return {sample.labels["window"]: sample.value for sample in family.samples}


def test_active_users_counts_per_window(monkeypatch):
    """Distinct-user counts are computed app-side from last-seen stamps —
    fixed 2-series cardinality regardless of user count (the whole point of
    replacing user_page_views_total)."""
    monkeypatch.setattr(metrics, "_user_last_seen", {})
    now = 1_800_000_000.0
    monkeypatch.setattr(metrics.time, "time", lambda: now)

    assert _active_counts() == {"1h": 0.0, "24h": 0.0}

    metrics.record_user_seen("111")
    metrics.record_user_seen("222")
    metrics.record_user_seen("111")  # repeat visits stay one user
    metrics._user_last_seen["333"] = now - 7200  # 2h ago: out of 1h, in 24h
    metrics._user_last_seen["444"] = now - 100_000  # out of both windows

    assert _active_counts() == {"1h": 2.0, "24h": 3.0}


def test_record_user_seen_prunes_stale_entries(monkeypatch):
    monkeypatch.setattr(metrics, "_user_last_seen", {})
    now = 1_800_000_000.0
    monkeypatch.setattr(metrics.time, "time", lambda: now)
    for i in range(2049):
        metrics._user_last_seen[str(i)] = now - 100_000  # all stale
    metrics.record_user_seen("fresh")
    assert metrics._user_last_seen == {"fresh": now}


def test_parse_age_bucket_boundaries():
    """Bucket labels for the parse-detail access analytics — the data that
    will decide whether old attack/damage-type detail can be pruned."""
    now = 1_800_000_000
    day = 86_400
    assert metrics.parse_age_bucket(None, now) == "unknown"
    assert metrics.parse_age_bucket(0, now) == "unknown"  # missing timestamp
    assert metrics.parse_age_bucket(now, now) == "<7d"
    assert metrics.parse_age_bucket(now - 6 * day, now) == "<7d"
    assert metrics.parse_age_bucket(now - 7 * day, now) == "7-30d"
    assert metrics.parse_age_bucket(now - 29 * day, now) == "7-30d"
    assert metrics.parse_age_bucket(now - 30 * day, now) == "30-90d"
    assert metrics.parse_age_bucket(now - 90 * day, now) == ">90d"
    assert metrics.parse_age_bucket(now + 500, now) == "<7d"  # clock skew clamps


# ── Observability additions (2026-10-07 architecture review) ─────────────────


class _StubPool:
    closed = False

    def __init__(self, stats):
        self._stats = stats

    def get_stats(self):
        return self._stats


def _pool_families():
    return {f.name: f for f in metrics._PgPoolCollector().collect()}


def test_pg_pool_collector_empty_when_pools_closed(monkeypatch):
    from backend import pg

    monkeypatch.setattr(pg, "_sync_pool", None)
    monkeypatch.setattr(pg, "_async_pool", None)
    assert list(metrics._PgPoolCollector().collect()) == []


def test_pg_pool_collector_yields_series_for_open_pools(monkeypatch):
    from backend import pg

    stats = {"pool_size": 5, "pool_available": 2, "requests_waiting": 3, "requests_wait_ms": 1234, "requests_errors": 1}
    monkeypatch.setattr(pg, "_sync_pool", _StubPool(stats))
    monkeypatch.setattr(pg, "_async_pool", None)
    fams = _pool_families()
    assert set(fams) == {
        "pg_pool_size",
        "pg_pool_available",
        "pg_pool_waiting",
        "pg_pool_requests_wait_ms",
        "pg_pool_requests_errors",
    }
    assert [(s.labels, s.value) for s in fams["pg_pool_waiting"].samples] == [({"pool": "sync"}, 3)]
    wait = fams["pg_pool_requests_wait_ms"].samples
    assert wait[0].name == "pg_pool_requests_wait_ms_total" and wait[0].value == 1234


def test_pg_pool_collector_labels_both_pools(monkeypatch):
    from backend import pg

    monkeypatch.setattr(pg, "_sync_pool", _StubPool({"pool_size": 1}))
    monkeypatch.setattr(pg, "_async_pool", _StubPool({"pool_size": 4}))
    samples = _pool_families()["pg_pool_size"].samples
    assert {s.labels["pool"]: s.value for s in samples} == {"sync": 1, "async": 4}


def test_event_loop_lag_metrics_exist_and_observe():
    metrics.EVENT_LOOP_LAG.set(0.25)
    assert metrics.EVENT_LOOP_LAG._value.get() == 0.25
    before = REGISTRY.get_sample_value("event_loop_lag_seconds_hist_count") or 0
    metrics.EVENT_LOOP_LAG_HIST.observe(0.02)
    assert REGISTRY.get_sample_value("event_loop_lag_seconds_hist_count") == before + 1
    assert REGISTRY.get_sample_value("event_loop_lag_seconds_hist_bucket", {"le": "0.05"}) is not None


def test_mark_loop_ok_sets_timestamp(monkeypatch):
    monkeypatch.setattr(metrics.time, "time", lambda: 1_800_000_000.0)
    metrics.mark_loop_ok("x")
    assert REGISTRY.get_sample_value("background_loop_last_success_timestamp_seconds", {"loop": "x"}) == 1_800_000_000.0


def test_rankings_metrics_exist():
    metrics.RANKINGS_REBUILD_SECONDS.labels(world="W").observe(3.0)
    metrics.RANKINGS_KILLS_DATASET_SIZE.labels(world="W").set(42)
    assert REGISTRY.get_sample_value("rankings_rebuild_seconds_count", {"world": "W"}) >= 1
    assert REGISTRY.get_sample_value("rankings_rebuild_seconds_bucket", {"world": "W", "le": "300.0"}) >= 1
    assert REGISTRY.get_sample_value("rankings_kills_dataset_size", {"world": "W"}) == 42


def test_sse_subscribers_gauge_tracks_subscribe_unsubscribe():
    import asyncio

    from backend.server import census_events

    census_events._reset_for_test()

    async def go():
        q1 = census_events.subscribe()
        q2 = census_events.subscribe()
        assert REGISTRY.get_sample_value("sse_subscribers") == 2
        census_events.unsubscribe(q1)
        assert REGISTRY.get_sample_value("sse_subscribers") == 1
        census_events.unsubscribe(q2)
        assert REGISTRY.get_sample_value("sse_subscribers") == 0

    asyncio.run(go())


def test_duration_buckets_reach_60s():
    assert metrics.HTTP_DURATION_BUCKETS == (0.01, 0.05, 0.1, 0.25, 1.0, 2.5, 5, 10, 30, 60)
    assert REGISTRY.get_sample_value("http_request_duration_seconds_bucket", {"path": "/x", "le": "60.0"}) is None
    metrics.HTTP_REQUEST_DURATION.labels(path="/x").observe(32)
    assert REGISTRY.get_sample_value("http_request_duration_seconds_bucket", {"path": "/x", "le": "60.0"}) == 1
    assert REGISTRY.get_sample_value("http_request_duration_seconds_bucket", {"path": "/x", "le": "30.0"}) == 0
