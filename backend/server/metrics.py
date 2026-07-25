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
import sqlite3
import time

from prometheus_client import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    Info,
    disable_created_metrics,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from backend.sql_loader import load_sql

# The OpenMetrics *_created companion series double the series count of every
# counter/histogram and nothing reads them (Grafana cardinality dashboard
# flagged them all "Unused"). Kill them globally before any metric is defined.
disable_created_metrics()

_SQL = load_sql(__file__)

_log = logging.getLogger(__name__)

# ── HTTP request metrics ──────────────────────────────────────────────────────
# Cardinality discipline (2026-07 Grafana free-tier overrun): labels only ever
# take bounded values — route templates via normalize_http_labels (never raw
# URL paths, which bot scans mint by the thousand) and a fixed method
# vocabulary. The latency histogram deliberately has NO method label: each
# extra label combo costs ~(buckets + 2) series and no dashboard queried it.

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests handled by the API",
    ["method", "path", "status_code"],
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 1.0, 2.5),
)

# ── Active users ──────────────────────────────────────────────────────────────
# Bounded replacement for the removed user_page_views_total (which cost
# username × path series — 12.9k at peak). The distinct-user count is computed
# app-side from in-memory last-seen timestamps and exported as ONE series per
# window by _ActiveUsersCollector, so cardinality is fixed regardless of how
# many users exist. Resets on deploy (in-memory), like every other gauge here.

_ACTIVE_WINDOWS: dict[str, float] = {"1h": 3600.0, "24h": 86400.0}
_user_last_seen: dict[str, float] = {}


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

# ── Application info ──────────────────────────────────────────────────────────

# Old metric kept alive for one release so Grafana dashboards have time to
# switch their filters from eq2_companion → eq2_lexicon. Drop in the next
# polish PR after dashboards have moved.
APP_INFO_LEGACY: Info = Info("eq2_companion", "DEPRECATED — use eq2_lexicon")
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
    Custom collector that runs fast COUNT queries against the local SQLite DBs
    each time Prometheus scrapes /metrics. SQLite COUNTs on indexed tables in
    the few-thousand-row range are sub-millisecond, so blocking the collector
    is fine.

    A 30-second scrape interval × ~12 queries × <1 ms each is ~12 ms/scrape
    of total DB work — well under any threshold worth caching for.

    BE-229: connections are kept open between scrapes (ro URI mode) to avoid
    the open/close overhead every 30 s.  A failed connection is retried on the
    next scrape (the dict slot is cleared on exception).
    """

    def __init__(self) -> None:
        self._conns: dict[str, sqlite3.Connection] = {}

    def _get_conn(self, name: str, path: object) -> sqlite3.Connection | None:
        """Return a cached read-only connection, opening it lazily.

        ``check_same_thread=False`` is required because Prometheus scrapes can
        arrive on different threads (uvicorn worker vs the request thread the
        first scrape happened on). Safe for our use because:
          1. Connections are opened in read-only (``?mode=ro``) URI mode — no
             writes ever happen on these connections, so SQLite's serialised
             write mode isn't entered.
          2. SQLite itself supports concurrent reads from multiple threads;
             ``check_same_thread`` is Python's conservative default safety
             guard, not a SQLite-level constraint.

        Without this flag, scrape #2 from a different thread than scrape #1
        crashes with ``sqlite3.ProgrammingError: SQLite objects created in a
        thread can only be used in that same thread``.
        """
        from pathlib import Path as _Path

        if not isinstance(path, _Path) or not path.exists():
            return None
        conn = self._conns.get(name)
        if conn is None:
            try:
                conn = sqlite3.connect(
                    f"file:{path}?mode=ro",
                    uri=True,
                    check_same_thread=False,
                )
                self._conns[name] = conn
            except Exception as exc:
                _log.warning("[metrics] failed to open %s: %s", name, exc)
                return None
        return conn

    def _close_conn(self, name: str) -> None:
        """Close and evict a connection (called on error to force re-open next scrape)."""
        conn = self._conns.pop(name, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def collect(self):  # type: ignore[override]
        # Lazy imports — keep metrics.py importable from tests without the
        # full DB modules loaded.
        from backend.eq2db import raids as raids_db
        from backend.server.db import DB_PATH as users_db_path
        from backend.server.parses import db as parses_db

        g_users = GaugeMetricFamily("users_total", "Registered users by access status", labels=["status"])
        g_claims = GaugeMetricFamily("character_claims_total", "Character claims by status", labels=["status"])
        g_parses = GaugeMetricFamily(
            "parses_encounters_total",
            "Total normalised encounters in parses.db (visible / hidden)",
            labels=["visibility"],
        )
        g_raids = GaugeMetricFamily(
            "raid_encounters_total",
            "Curated raid-encounter strategy rows in raids.db",
        )
        g_triggers = GaugeMetricFamily(
            "act_triggers_total",
            "ACT triggers stored across all encounters",
        )
        g_spell_timers = GaugeMetricFamily(
            "act_spell_timers_total",
            "ACT spell-timer definitions stored across all encounters",
        )

        # users.db ----------------------------------------------------------
        conn = self._get_conn("users", users_db_path)
        if conn is not None:
            try:
                for status in ("approved", "pending", "denied"):
                    row = conn.execute(_SQL["count_users_by_access_status"], (status,)).fetchone()
                    g_users.add_metric([status], row[0] if row else 0)

                for status in ("pending", "approved", "rejected", "withdrawn", "superseded"):
                    row = conn.execute(_SQL["count_claims_by_status"], (status,)).fetchone()
                    g_claims.add_metric([status], row[0] if row else 0)
            except Exception:
                _log.exception("[metrics] users.db collector error")
                self._close_conn("users")

        # parses.db — encounters split by hidden_at (visible vs soft-deleted)
        # so dashboards can distinguish "live leaderboard rows" from
        # accumulated history.
        conn = self._get_conn("parses", parses_db.DB_PATH)
        if conn is not None:
            try:
                row = conn.execute(_SQL["count_visible_encounters"]).fetchone()
                g_parses.add_metric(["visible"], row[0] if row else 0)
                row = conn.execute(_SQL["count_hidden_encounters"]).fetchone()
                g_parses.add_metric(["hidden"], row[0] if row else 0)
            except Exception:
                _log.exception("[metrics] parses.db collector error")
                self._close_conn("parses")

        # raids.db — strategies + the ACT trigger pack.
        conn = self._get_conn("raids", raids_db.DB_PATH)
        if conn is not None:
            try:
                row = conn.execute(_SQL["count_raid_encounters"]).fetchone()
                g_raids.add_metric([], row[0] if row else 0)
                row = conn.execute(_SQL["count_act_triggers"]).fetchone()
                g_triggers.add_metric([], row[0] if row else 0)
                row = conn.execute(_SQL["count_act_spell_timers"]).fetchone()
                g_spell_timers.add_metric([], row[0] if row else 0)
            except Exception:
                _log.exception("[metrics] raids.db collector error")
                self._close_conn("raids")

        yield g_users
        yield g_claims
        yield g_parses
        yield g_raids
        yield g_triggers
        yield g_spell_timers


class _DBFileSizeCollector(Collector):
    """File-size gauge for every SQLite DB the app reads/writes. Lets the
    Databases dashboard show growth trends per DB without per-table COUNTs
    (those live in :class:`_DBCollector`).

    Inspects only what's on disk; doesn't touch the DBs. Missing DBs are
    silently absent from the output rather than reporting 0 — a missing
    file is a different state than an empty one, and the dashboard can
    spot the difference via the labelset gap."""

    def collect(self):  # type: ignore[override]
        from backend.census import store as census_store
        from backend.eq2db import classes as classes_db
        from backend.eq2db import items as items_db
        from backend.eq2db import raids as raids_db
        from backend.eq2db import recipes as recipes_db
        from backend.eq2db import spells as spells_db
        from backend.eq2db import zones as zones_db
        from backend.server.db import DB_PATH as users_db_path
        from backend.server.parses import db as parses_db

        # Map label → Path. Centralised so adding a new DB is one tuple.
        candidates = [
            ("users", users_db_path),
            ("parses", parses_db.DB_PATH),
            ("census", census_store.DB_PATH),
            ("raids", raids_db.DB_PATH),
            ("zones", zones_db.DB_PATH),
            ("items", items_db.DB_PATH),
            ("spells", spells_db.DB_PATH),
            ("recipes", recipes_db.DB_PATH),
            ("classes", classes_db.DB_PATH),
        ]

        g_size = GaugeMetricFamily(
            "db_file_size_bytes",
            "On-disk size of each SQLite database (bytes)",
            labels=["db"],
        )

        for label, path in candidates:
            try:
                if path.exists():
                    g_size.add_metric([label], path.stat().st_size)
            except Exception:
                _log.exception("[metrics] db file-size for %s", label)

        yield g_size


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
        REGISTRY.register(_DBFileSizeCollector())
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
    ``web.routes.parses._validate_payload_signature`` which uses the same
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
