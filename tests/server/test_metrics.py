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
