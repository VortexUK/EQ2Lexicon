"""
Prometheus metric definitions for the EQ2 Companion web app.

All metric objects live here so they are created exactly once and can be
imported by any module that needs to increment them.

Exposed at  GET /metrics  (Prometheus text format).
Optional token auth: set METRICS_TOKEN env var; if empty, the endpoint
is open (fine for a private Railway service).
"""

from __future__ import annotations

import hmac as _hmac
import logging
import os
import re
import time

from prometheus_client import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    Info,
    disable_created_metrics,
)
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.registry import Collector

from backend.sql_loader import load_sql

# The OpenMetrics *_created companion series double the series count of every
# counter/histogram and nothing reads them (Grafana cardinality dashboard
# flagged them all "Unused"). Kill them globally before any metric is defined.
disable_created_metrics()

_SQL = load_sql(__file__)

_log = logging.getLogger(__name__)

# ── HTTP request metrics ──────────────────────────────────────────────────────
# Cardinality discipline (series limits on the metrics backend): labels only ever
# take bounded values — route templates via normalize_http_labels (never raw
# URL paths, which bot scans mint by the thousand) and a fixed method
# vocabulary. The latency histogram deliberately has NO method label: each
# extra label combo costs ~(buckets + 2) series and no dashboard queried it.

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests handled by the API",
    ["method", "path", "status_code"],
)

# Top buckets reach 60 s so the known ~32 s cold builds read as a bucket, not
# ">2.5s". The 0.5 / 0.75 / 1.5 steps exist because histogram_quantile
# interpolates inside a bucket: with a 0.25 → 1.0 gap, one slow request read
# as a 962 ms p95 regardless of whether it took 300 ms or 900 ms.
HTTP_DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.5, 5, 10, 30, 60)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["path"],
    buckets=HTTP_DURATION_BUCKETS,
)

# ── Active users ──────────────────────────────────────────────────────────────
# Bounded replacement for the removed user_page_views_total (which cost
# username × path series — 12.9k at peak). The distinct-user count is computed
# app-side from in-memory last-seen timestamps and exported as ONE series per
# window by _ActiveUsersCollector, so cardinality is fixed regardless of how
# many users exist. Resets on deploy (in-memory), like every other gauge here.

_ACTIVE_WINDOWS: dict[str, float] = {"1h": 3600.0, "24h": 86400.0}
_user_last_seen: dict[str, float] = {}


def forget_user(user_id: str) -> None:
    """Drop a user from the in-memory active map — account erasure must not
    leave their id in process memory either."""
    _user_last_seen.pop(user_id, None)


def record_user_seen(user_id: str) -> None:
    """Stamp an authenticated user as active now (called from the metrics
    middleware). Dict ops are atomic under the GIL; scrape-side reads snapshot."""
    now = time.time()
    _user_last_seen[user_id] = now
    # Prune only if the dict somehow grows far past the real user count so a
    # long-lived process can't accumulate unboundedly.
    if len(_user_last_seen) > 2048:
        cutoff = now - max(_ACTIVE_WINDOWS.values())
        for key in [k for k, ts in _user_last_seen.items() if ts < cutoff]:
            _user_last_seen.pop(key, None)


class _ActiveUsersCollector(Collector):
    """Emit active_users{window=} at scrape time from the last-seen map."""

    def collect(self):  # type: ignore[override]
        g = GaugeMetricFamily(
            "active_users",
            "Distinct authenticated users seen within the trailing window",
            labels=["window"],
        )
        now = time.time()
        stamps = list(_user_last_seen.values())
        for label, span in _ACTIVE_WINDOWS.items():
            g.add_metric([label], float(sum(1 for ts in stamps if now - ts <= span)))
        yield g


# ── Cache metrics ─────────────────────────────────────────────────────────────
# Labels: cache = character | guild | claim

# How often parse DETAIL pages are opened, bucketed by the encounter's
# age at view time — the evidence for the per-ability detail retention
# windows (parses/cleanup.py). Buckets: <7d, 7-30d, 30-90d, >90d.
PARSE_DETAIL_VIEWS = Counter(
    "parse_detail_views_total",
    "Parse detail page loads by encounter age at view time",
    ["age_bucket"],
)


def parse_age_bucket(started_at: int | None, now: int) -> str:
    """Age bucket label for PARSE_DETAIL_VIEWS."""
    if not started_at:
        return "unknown"
    days = max(0, now - int(started_at)) / 86400
    if days < 7:
        return "<7d"
    if days < 30:
        return "7-30d"
    if days < 90:
        return "30-90d"
    return ">90d"


CACHE_HITS = Counter("cache_hits_total", "Fresh cache hits", ["cache"])
CACHE_MISSES = Counter("cache_misses_total", "Cache misses (not found or expired)", ["cache"])
CACHE_STALE = Counter("cache_stale_total", "Stale hits that fired bg refresh", ["cache"])
# A memory miss that census_store then served instantly — the dashboard's
# "miss" panel splits into store-absorbed vs real Census fetches with this.
CACHE_STORE_HITS = Counter("cache_store_hits_total", "Misses served from the durable census_store", ["cache"])
CACHE_SETS = Counter("cache_sets_total", "Values written into cache", ["cache"])
CACHE_SIZE = Gauge("cache_size", "Live entry count in cache", ["cache"])

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

# ── Event loop, background loops, rankings, SSE ───────────────────────────────

EVENT_LOOP_LAG = Gauge("event_loop_lag_seconds", "Most recent event-loop scheduling lag sample (seconds)")
EVENT_LOOP_LAG_HIST = Histogram(
    "event_loop_lag_seconds_hist",
    "Distribution of event-loop scheduling lag samples (seconds)",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10),
)

BACKGROUND_LOOP_LAST_SUCCESS = Gauge(
    "background_loop_last_success_timestamp_seconds",
    "Unix time a lifespan background loop last completed an iteration without raising",
    ["loop"],
)


def mark_loop_ok(name: str) -> None:
    """Stamp a background loop's iteration as successfully completed now.
    ``name`` must come from a fixed set of loop names (bounded cardinality)."""
    BACKGROUND_LOOP_LAST_SUCCESS.labels(loop=name).set(time.time())


RANKINGS_REBUILD_SECONDS = Histogram(
    "rankings_rebuild_seconds",
    "Wall time of a full rankings kills-dataset rebuild",
    ["world"],
    buckets=(1, 2, 5, 10, 30, 60, 120, 300),
)
RANKINGS_KILLS_DATASET_SIZE = Gauge(
    "rankings_kills_dataset_size",
    "Number of kills in the last rankings dataset build",
    ["world"],
)

SSE_SUBSCRIBERS = Gauge("sse_subscribers", "Currently connected census SSE stream clients")

# ── Application info ──────────────────────────────────────────────────────────

APP_INFO: Info = Info("eq2_lexicon", "Per-deployment app info (world, version).")

# ── App-level error counter ───────────────────────────────────────────────────
# Bumped by the FastAPI exception handler for unhandled 500s. 4xx user-errors
# (auth, validation) deliberately don't count here — they'd drown out the
# server-side problems this metric is meant to surface.

APP_ERRORS = Counter(
    "app_errors_total",
    "Server-side errors (unhandled exceptions or explicit 500s)",
    ["source"],
)

# ── DB gauges (collected on-demand) ──────────────────────────────────────────


class _DBCollector(Collector):
    """
    Custom collector that runs fast COUNT queries against the migrated
    Postgres family schemas each time Prometheus scrapes /metrics. Tiny
    COUNTs per scrape; a short-lived pooled checkout per family keeps the
    collector simple.
    """

    def collect(self):  # type: ignore[override]
        # Lazy imports — keep metrics.py importable from tests without the
        # full DB modules loaded.
        from backend import pg
        from backend.eq2db import raids as raids_db
        from backend.server.db import SCHEMA as users_schema
        from backend.server.parses import db as parses_db

        g_users = GaugeMetricFamily("users_total", "Registered users by access status", labels=["status"])
        g_claims = GaugeMetricFamily("character_claims_total", "Character claims by status", labels=["status"])
        g_parses = GaugeMetricFamily(
            "parses_encounters_total",
            "Total normalised encounters in the parses schema (visible / hidden)",
            labels=["visibility"],
        )
        g_raids = GaugeMetricFamily(
            "raid_encounters_total",
            "Curated raid-encounter strategy rows in the raids schema",
        )
        g_triggers = GaugeMetricFamily(
            "act_triggers_total",
            "ACT triggers stored across all encounters",
        )
        g_spell_timers = GaugeMetricFamily(
            "act_spell_timers_total",
            "ACT spell-timer definitions stored across all encounters",
        )

        # users schema (Postgres) --------------------------------------------
        # Tiny COUNTs per scrape; a short-lived pooled checkout beats keeping
        # a scrape-lifetime connection around. (The P2 metrics split replaces
        # this with 60s-cached counts.)
        try:
            with pg.connection(users_schema, autocommit=True) as conn:
                for status in ("approved", "pending", "denied"):
                    row = conn.execute(_SQL["count_users_by_access_status"], (status,)).fetchone()
                    g_users.add_metric([status], row["n"] if row else 0)
                for status in ("pending", "approved", "rejected", "withdrawn", "superseded"):
                    row = conn.execute(_SQL["count_claims_by_status"], (status,)).fetchone()
                    g_claims.add_metric([status], row["n"] if row else 0)
        except Exception:
            _log.exception("[metrics] users-schema collector error")

        # parses schema (Postgres) — encounters split by hidden_at (visible vs
        # soft-deleted) so dashboards can distinguish "live leaderboard rows"
        # from accumulated history.
        try:
            with pg.connection(parses_db.SCHEMA, autocommit=True) as conn:
                row = conn.execute(_SQL["count_visible_encounters"]).fetchone()
                g_parses.add_metric(["visible"], row["n"] if row else 0)
                row = conn.execute(_SQL["count_hidden_encounters"]).fetchone()
                g_parses.add_metric(["hidden"], row["n"] if row else 0)
        except Exception:
            _log.exception("[metrics] parses-schema collector error")

        # raids schema (Postgres) — strategies + the ACT trigger pack.
        try:
            with pg.connection(raids_db.SCHEMA, autocommit=True) as conn:
                row = conn.execute(_SQL["count_raid_encounters"]).fetchone()
                g_raids.add_metric([], row["n"] if row else 0)
                row = conn.execute(_SQL["count_act_triggers"]).fetchone()
                g_triggers.add_metric([], row["n"] if row else 0)
                row = conn.execute(_SQL["count_act_spell_timers"]).fetchone()
                g_spell_timers.add_metric([], row["n"] if row else 0)
        except Exception:
            _log.exception("[metrics] raids-schema collector error")

        yield g_users
        yield g_claims
        yield g_parses
        yield g_raids
        yield g_triggers
        yield g_spell_timers


class _PgSchemaSizeCollector(Collector):
    """Total relation size per Postgres family schema — the growth-trend
    gauge behind the Databases dashboard."""

    def collect(self):  # type: ignore[override]
        g_pg = GaugeMetricFamily(
            "pg_schema_size_bytes",
            "Total relation size per migrated Postgres family schema (bytes)",
            labels=["schema"],
        )
        try:
            from backend import pg

            with pg.connection(autocommit=True) as conn:
                rows = conn.execute(
                    "SELECT schemaname AS s,"
                    " SUM(pg_total_relation_size((quote_ident(schemaname) || '.' || quote_ident(tablename))::regclass))::bigint AS b"
                    " FROM pg_tables WHERE schemaname IN"
                    " ('users', 'parses', 'census', 'zones', 'raids', 'items', 'spells', 'recipes', 'aas', 'classes')"
                    " GROUP BY schemaname"
                ).fetchall()
            for r in rows:
                g_pg.add_metric([r["s"]], r["b"] or 0)
        except Exception:
            _log.exception("[metrics] pg schema-size collector error")
        yield g_pg


class _PgRoundTripCollector(Collector):
    """One timed ``SELECT 1`` on an already-open pooled connection per
    scrape: the app-to-Postgres network round trip, which is the unit every
    pooled call is priced in. Autocommit and no schema change, so the
    measurement is exactly one round trip."""

    def collect(self):  # type: ignore[override]
        from backend import pg

        g = GaugeMetricFamily("pg_roundtrip_seconds", "Latency of one SELECT 1 round trip to Postgres")
        if pg._sync_pool is None or getattr(pg._sync_pool, "closed", False):
            return
        try:
            with pg.connection(autocommit=True) as conn:
                t0 = time.perf_counter()
                conn.execute("SELECT 1").fetchone()
                g.add_metric([], time.perf_counter() - t0)
        except Exception:
            _log.exception("[metrics] pg round-trip probe error")
            return
        yield g


class _PgPoolCollector(Collector):
    """psycopg pool saturation stats for the sync and async pools, read from
    ``pool.get_stats()`` at scrape time. Pools that aren't open yield nothing."""

    def collect(self):  # type: ignore[override]
        from backend import pg

        size = GaugeMetricFamily("pg_pool_size", "Connections currently in the pool", labels=["pool"])
        avail = GaugeMetricFamily("pg_pool_available", "Idle connections available in the pool", labels=["pool"])
        waiting = GaugeMetricFamily("pg_pool_waiting", "Requests queued waiting for a connection", labels=["pool"])
        wait_ms = CounterMetricFamily(
            "pg_pool_requests_wait_ms", "Cumulative ms requests spent waiting for a connection", labels=["pool"]
        )
        errors = CounterMetricFamily(
            "pg_pool_requests_errors", "Cumulative connection requests that failed (timeout etc.)", labels=["pool"]
        )
        for label, pool in (("sync", pg._sync_pool), ("async", pg._async_pool)):
            if pool is None or getattr(pool, "closed", False):
                continue
            try:
                st = pool.get_stats()
            except Exception:
                _log.exception("[metrics] pg pool stats error pool=%s", label)
                continue
            size.add_metric([label], st.get("pool_size", 0))
            avail.add_metric([label], st.get("pool_available", 0))
            waiting.add_metric([label], st.get("requests_waiting", 0))
            wait_ms.add_metric([label], st.get("requests_wait_ms", 0))
            errors.add_metric([label], st.get("requests_errors", 0))
        if size.samples:
            yield from (size, avail, waiting, wait_ms, errors)


class _CensusHealthCollector(Collector):
    """Read the in-memory census-health state at scrape time and surface it
    as a gauge (1 = up, 0 = down/unknown). Avoids needing a feedback hook
    from census_health into the metrics module."""

    def collect(self):  # type: ignore[override]
        from backend.server import census_health

        g = GaugeMetricFamily(
            "census_health_status",
            "Census API health (1 = up, 0 = down/unknown)",
        )
        state = census_health.get_state()
        g.add_metric([], 1.0 if state.get("status") == "up" else 0.0)
        yield g


# Register once — guarded so re-imports in tests don't raise DuplicateCollector
_db_collector_registered = False


def _register_db_collector() -> None:
    """Register the on-scrape collectors. Called once from FastAPI startup."""
    global _db_collector_registered
    if not _db_collector_registered:
        REGISTRY.register(_DBCollector())
        REGISTRY.register(_PgSchemaSizeCollector())
        REGISTRY.register(_PgPoolCollector())
        REGISTRY.register(_PgRoundTripCollector())
        REGISTRY.register(_CensusHealthCollector())
        REGISTRY.register(_ActiveUsersCollector())
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
    "/class-icons/",
    "/metrics",
)


def should_track_path(path: str) -> bool:
    return not any(path.startswith(p) for p in _SKIP_PREFIXES)


# ── Label normalisation (cardinality guard) ──────────────────────────────────
# Requests that match no route (bot probes with POST/PUT on arbitrary paths)
# have no template — labelling them with the raw URL mints a permanent series
# per probe path (prometheus_client never forgets a label combo). Collapse
# them all into one bucket; ditto garbage HTTP verbs (PROPFIND, TRACK, …).

UNMATCHED_PATH = "(unmatched)"

_KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


def normalize_http_labels(method: str, route_path: str | None) -> tuple[str, str]:
    """Return (method, path) label values with bounded cardinality."""
    method = method.upper()
    if method not in _KNOWN_METHODS:
        method = "OTHER"
    return method, route_path if route_path else UNMATCHED_PATH


# ── Token check ───────────────────────────────────────────────────────────────

METRICS_TOKEN: str = os.getenv("METRICS_TOKEN", "")


def check_metrics_auth(authorization: str | None) -> bool:
    """Return True if the request is authorised to view /metrics.

    Uses ``hmac.compare_digest`` to avoid the timing-attack window that ``==``
    on the token string would open. Consistent with
    ``backend.server.api.parses._validate_payload_signature`` which uses the same
    helper for the plugin-upload HMAC.
    """
    if not METRICS_TOKEN:
        return True  # no token configured → open access
    if not authorization:
        return False
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return _hmac.compare_digest(token, METRICS_TOKEN)
