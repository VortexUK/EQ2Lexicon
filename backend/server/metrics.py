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

from prometheus_client import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    Info,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

_log = logging.getLogger(__name__)

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

# ── Per-user page view metrics ─────────────────────────────────────────────────
# Tracks authenticated GET requests by Discord username and route template.
# Cardinality: ~users × ~routes — stays well under 1 k series for a guild tool.

USER_PAGE_VIEWS = Counter(
    "user_page_views_total",
    "Successful authenticated GET requests by user and route",
    ["username", "path"],
)

# ── Cache metrics ─────────────────────────────────────────────────────────────
# Labels: cache = character | guild | claim

CACHE_HITS = Counter("cache_hits_total", "Fresh cache hits", ["cache"])
CACHE_MISSES = Counter("cache_misses_total", "Cache misses (not found or expired)", ["cache"])
CACHE_STALE = Counter("cache_stale_total", "Stale hits that fired bg refresh", ["cache"])
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
                    row = conn.execute(
                        "SELECT COUNT(*) FROM users WHERE access_status = ?",
                        (status,),
                    ).fetchone()
                    g_users.add_metric([status], row[0] if row else 0)

                for status in ("pending", "approved", "rejected", "withdrawn", "superseded"):
                    row = conn.execute(
                        "SELECT COUNT(*) FROM character_claims WHERE status = ?",
                        (status,),
                    ).fetchone()
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
                row = conn.execute("SELECT COUNT(*) FROM encounters WHERE hidden_at IS NULL").fetchone()
                g_parses.add_metric(["visible"], row[0] if row else 0)
                row = conn.execute("SELECT COUNT(*) FROM encounters WHERE hidden_at IS NOT NULL").fetchone()
                g_parses.add_metric(["hidden"], row[0] if row else 0)
            except Exception:
                _log.exception("[metrics] parses.db collector error")
                self._close_conn("parses")

        # raids.db — strategies + the ACT trigger pack.
        conn = self._get_conn("raids", raids_db.DB_PATH)
        if conn is not None:
            try:
                row = conn.execute("SELECT COUNT(*) FROM raid_encounters").fetchone()
                g_raids.add_metric([], row[0] if row else 0)
                row = conn.execute("SELECT COUNT(*) FROM act_triggers").fetchone()
                g_triggers.add_metric([], row[0] if row else 0)
                row = conn.execute("SELECT COUNT(*) FROM act_spell_timers").fetchone()
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

# Routes excluded from per-user page-view tracking (still counted in
# HTTP_REQUESTS).  Add polling/background endpoints here so they don't
# inflate individual user activity graphs.
_USER_VIEW_SKIP = frozenset(
    [
        "/api/notifications",
    ]
)


def should_track_path(path: str) -> bool:
    return not any(path.startswith(p) for p in _SKIP_PREFIXES)


def should_track_user_view(route_path: str) -> bool:
    """Return False for background/polling routes that shouldn't count as
    meaningful page views in the per-user USER_PAGE_VIEWS metric."""
    return route_path not in _USER_VIEW_SKIP


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
