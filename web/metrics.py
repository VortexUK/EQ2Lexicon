"""
Prometheus metric definitions for the EQ2 Companion web app.

All metric objects live here so they are created exactly once and can be
imported by any module that needs to increment them.

Exposed at  GET /metrics  (Prometheus text format).
Optional token auth: set METRICS_TOKEN env var; if empty, the endpoint
is open (fine for a private Railway service).
"""
from __future__ import annotations

import os
import re
import sqlite3

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    Info,
    REGISTRY,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

# ── HTTP request metrics ──────────────────────────────────────────────────────

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests handled by the API",
    ["method", "path", "status_code"],
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# ── Cache metrics ─────────────────────────────────────────────────────────────
# Labels: cache = character | guild | claim

CACHE_HITS   = Counter("cache_hits_total",   "Fresh cache hits",                     ["cache"])
CACHE_MISSES = Counter("cache_misses_total",  "Cache misses (not found or expired)",  ["cache"])
CACHE_STALE  = Counter("cache_stale_total",   "Stale hits that fired bg refresh",     ["cache"])
CACHE_SETS   = Counter("cache_sets_total",    "Values written into cache",            ["cache"])
CACHE_SIZE   = Gauge("cache_size",            "Live entry count in cache",            ["cache"])

# ── Census API metrics ────────────────────────────────────────────────────────
# endpoint label: character | guild | item | (unknown)
# status  label: success | http_error | error

CENSUS_REQUESTS = Counter(
    "census_api_requests_total",
    "Requests sent to the Daybreak Census API",
    ["endpoint", "status"],
)

CENSUS_DURATION = Histogram(
    "census_api_duration_seconds",
    "Round-trip latency for Census API calls",
    ["endpoint"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

# ── Application info ──────────────────────────────────────────────────────────

APP_INFO: Info = Info("eq2_companion", "Static build / configuration info")

# ── DB gauges (collected on-demand) ──────────────────────────────────────────


class _DBCollector(Collector):
    """
    Custom collector that runs fast COUNT queries against the local SQLite DB
    each time Prometheus scrapes /metrics.  SQLite queries on a tiny DB are
    < 1 ms so blocking the collector is acceptable.
    """

    def collect(self):  # type: ignore[override]
        from web.db import DB_PATH

        g_users = GaugeMetricFamily(
            "users_total", "Registered users by access status", labels=["status"]
        )
        g_claims = GaugeMetricFamily(
            "character_claims_total", "Character claims by status", labels=["status"]
        )

        try:
            conn = sqlite3.connect(DB_PATH, timeout=1.0)
            for status in ("approved", "pending", "denied"):
                row = conn.execute(
                    "SELECT COUNT(*) FROM users WHERE access_status = ?", (status,)
                ).fetchone()
                g_users.add_metric([status], row[0] if row else 0)

            for status in ("pending", "approved", "rejected", "withdrawn", "superseded"):
                row = conn.execute(
                    "SELECT COUNT(*) FROM character_claims WHERE status = ?", (status,)
                ).fetchone()
                g_claims.add_metric([status], row[0] if row else 0)

            conn.close()
        except Exception as exc:
            print(f"[metrics] DB collector error: {exc}")

        yield g_users
        yield g_claims


# Register once — guarded so re-imports in tests don't raise DuplicateCollector
_db_collector_registered = False

def _register_db_collector() -> None:
    global _db_collector_registered
    if not _db_collector_registered:
        REGISTRY.register(_DBCollector())
        _db_collector_registered = True


# ── Helpers ───────────────────────────────────────────────────────────────────

_CENSUS_ENDPOINT_RE = re.compile(r"/json/get/eq2/([^/?]+)")

def census_endpoint_label(url: str) -> str:
    """Extract the Census collection name (character, guild, item …) from a URL."""
    m = _CENSUS_ENDPOINT_RE.search(url)
    return m.group(1) if m else "unknown"


# ── Paths to exclude from HTTP metrics (static assets, self) ─────────────────

_SKIP_PREFIXES = (
    "/assets/",
    "/icons/",
    "/aa-assets/",
    "/spell-icons/",
    "/metrics",
)


def should_track_path(path: str) -> bool:
    return not any(path.startswith(p) for p in _SKIP_PREFIXES)


# ── Token check ───────────────────────────────────────────────────────────────

METRICS_TOKEN: str = os.getenv("METRICS_TOKEN", "")


def check_metrics_auth(authorization: str | None) -> bool:
    """Return True if the request is authorised to view /metrics."""
    if not METRICS_TOKEN:
        return True   # no token configured → open access
    if not authorization:
        return False
    scheme, _, token = authorization.partition(" ")
    return scheme.lower() == "bearer" and token == METRICS_TOKEN
