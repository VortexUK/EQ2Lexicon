"""Tests for the metrics cardinality guards (2026-07 Grafana free-tier overrun).

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
