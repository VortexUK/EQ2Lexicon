from __future__ import annotations

import logging
import os
import threading
import time

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response

load_dotenv()
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from web import db as users_db
from web.cache import aa_cache, character_cache, claim_cache, guild_cache
from web.config import CORS_ORIGINS as _CORS_ORIGINS
from web.config import WORLD as _WORLD
from web.limiter import limiter
from web.metrics import (
    APP_INFO,
    CONTENT_TYPE_LATEST,
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    USER_PAGE_VIEWS,
    _register_db_collector,
    check_metrics_auth,
    generate_latest,
    should_track_path,
    should_track_user_view,
)
from web.routes.aa import router as aa_router
from web.routes.admin import router as admin_router
from web.routes.auth import router as auth_router
from web.routes.auth_tokens import router as auth_tokens_router
from web.routes.character import prewarm_character_cache
from web.routes.character import router as character_router
from web.routes.characters import router as characters_router
from web.routes.claim import router as claim_router
from web.routes.guild import router as guild_router
from web.routes.guild_officer import router as guild_officer_router
from web.routes.health import router as health_router
from web.routes.item import router as item_router
from web.routes.item_watch import router as item_watch_router
from web.routes.notifications import router as notifications_router
from web.routes.parses import router as parses_router
from web.routes.recipes import router as recipes_router

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Item-stats startup check
# ---------------------------------------------------------------------------


def _ensure_item_stats() -> None:
    """
    Called in a background thread at startup.

    * Creates the item_stats table / indexes if missing (idempotent).
    * If the table is empty but the items table has data, runs the stats
      backfill automatically.  This happens once after a fresh deployment
      or after the code is upgraded on an existing DB.
    """
    import sqlite3

    from census.db import DB_PATH as items_db_path
    from census.db import init_db as items_init_db

    if not items_db_path.exists():
        return  # No items DB yet — nothing to initialise

    try:
        items_init_db(items_db_path)  # creates tables/indexes if missing

        conn = sqlite3.connect(items_db_path)
        stat_count = conn.execute("SELECT COUNT(*) FROM item_stats").fetchone()[0]
        item_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        conn.close()

        if stat_count == 0 and item_count > 0:
            _log.info(
                "[startup] item_stats is empty (%d items) — running background backfill…",
                item_count,
            )
            # Ensure repo root is on sys.path so the scripts package is importable
            import sys

            repo_root = str(Path(__file__).resolve().parent.parent)
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from scripts.backfill_item_stats import run as _backfill  # type: ignore[import]

            _backfill(rebuild=False)
            _log.info("[startup] item_stats backfill complete.")

    except Exception as exc:
        _log.error("[startup] item_stats init/backfill error: %s", exc)


# ---------------------------------------------------------------------------
# HTTP metrics middleware
# ---------------------------------------------------------------------------


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds defensive HTTP security headers to every response."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), camera=(), microphone=()",
        )
        if _HTTPS_ONLY:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


class _MetricsMiddleware(BaseHTTPMiddleware):
    """Records per-route request count and latency using the matched route
    template (e.g. /api/character/{name}) rather than the raw path, so
    label cardinality stays low."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start

        path = request.url.path
        if should_track_path(path):
            # Prefer the route template; fall back to raw path
            route = request.scope.get("route")
            label_path = getattr(route, "path", path)
            HTTP_REQUESTS.labels(
                method=request.method,
                path=label_path,
                status_code=str(response.status_code),
            ).inc()
            HTTP_REQUEST_DURATION.labels(
                method=request.method,
                path=label_path,
            ).observe(elapsed)

            # Per-user page views: authenticated GET requests only.
            # Session is already populated by SessionMiddleware (runs before us).
            # Polling/background endpoints are excluded via should_track_user_view.
            if request.method == "GET" and response.status_code < 400 and should_track_user_view(label_path):
                user = request.session.get("user")
                if user:
                    USER_PAGE_VIEWS.labels(
                        username=user.get("username", "unknown"),
                        path=label_path,
                    ).inc()

        return response


# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
_ICONS_DIR = Path(__file__).resolve().parent.parent / "data" / "items" / "icons"
_AA_ASSETS_DIR = Path(__file__).resolve().parent.parent / "data" / "AAs"
_SPELL_ICONS_DIR = Path(__file__).resolve().parent.parent / "data" / "spells" / "icons"

_SESSION_SECRET = os.getenv("SESSION_SECRET", "")
if not _SESSION_SECRET:
    raise RuntimeError(
        "SESSION_SECRET environment variable is not set. "
        'Generate one with: python -c "import secrets; print(secrets.token_hex(32))" '
        "and add it to your .env file or Railway environment."
    )
if len(_SESSION_SECRET) < 32:
    raise RuntimeError(
        "SESSION_SECRET is too short (minimum 32 characters). "
        'Generate a secure value with: python -c "import secrets; print(secrets.token_hex(32))"'
    )

# Set HTTPS_ONLY=false only for local HTTP dev; defaults to true (Secure cookie flag)
_HTTPS_ONLY = os.getenv("HTTPS_ONLY", "true").lower() not in ("0", "false", "no")

# Set SHOW_API_DOCS=true to expose /api/docs and /api/openapi.json (off by default)
_SHOW_DOCS = os.getenv("SHOW_API_DOCS", "false").lower() in ("1", "true", "yes")


def create_app(session_secret: str | None = None) -> FastAPI:
    def _startup() -> None:
        users_db.init_db()
        # Run the item-stats check in a background thread so it never blocks
        # startup or Railway health checks.  On a fresh deployment the backfill
        # may take ~60–90 s; stat-filter searches will return 0 results until it
        # finishes, but name/tier/slot/class/level searches work immediately.
        t = threading.Thread(target=_ensure_item_stats, daemon=True, name="item-stats-backfill")
        t.start()

    async def _prewarm() -> None:
        # Fire-and-forget: pre-warm the character cache in the background so the
        # home page loads instantly even immediately after a redeploy.
        import asyncio as _asyncio

        _asyncio.create_task(prewarm_character_cache())
        _asyncio.create_task(_cache_sweep_loop())

    async def _cache_sweep_loop() -> None:
        """Periodically evict max_age-expired entries from all caches.
        Without this, entries for keys that are never accessed again stay in
        memory until the process restarts."""
        import asyncio as _asyncio

        while True:
            await _asyncio.sleep(600)  # run every 10 minutes
            for cache in (character_cache, guild_cache, claim_cache, aa_cache):
                cache.sweep()

    def _init_metrics() -> None:
        # Register the DB gauge collector and set static app info.
        _register_db_collector()
        APP_INFO.info({"world": _WORLD, "version": "0.1.0"})

    app = FastAPI(
        on_startup=[_startup, _prewarm, _init_metrics],
        title="EQ2 TLE Companion",
        version="0.1.0",
        docs_url="/api/docs" if _SHOW_DOCS else None,
        redoc_url="/api/redoc" if _SHOW_DOCS else None,
        openapi_url="/api/openapi.json" if _SHOW_DOCS else None,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    # Outermost: security headers on every response
    app.add_middleware(_SecurityHeadersMiddleware)

    # Metrics middleware (added last = outermost after security headers)
    app.add_middleware(_MetricsMiddleware)

    # Sessions must be added before CORS so the cookie is available everywhere.
    # same_site="lax" is required for the Discord OAuth callback to receive the
    # session cookie (strict would block the cookie on the redirect from
    # discord.com → /api/auth/callback, breaking CSRF state validation).
    # Lax still blocks cross-site POST/DELETE (the CSRF vector we care about).
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret or _SESSION_SECRET,
        https_only=_HTTPS_ONLY,
        same_site="lax",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routers
    app.include_router(health_router, prefix="/api")
    app.include_router(auth_router, prefix="/api")
    app.include_router(auth_tokens_router, prefix="/api")
    app.include_router(character_router, prefix="/api")
    app.include_router(item_router, prefix="/api")
    app.include_router(claim_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    app.include_router(guild_router, prefix="/api")
    app.include_router(guild_officer_router, prefix="/api")
    app.include_router(item_watch_router, prefix="/api")
    app.include_router(characters_router, prefix="/api")
    app.include_router(aa_router, prefix="/api")
    app.include_router(notifications_router, prefix="/api")
    app.include_router(recipes_router, prefix="/api")
    app.include_router(parses_router, prefix="/api")

    # ── /metrics — Prometheus text format ───────────────────────────────────
    # Runs synchronously (FastAPI auto-offloads sync def to a thread pool)
    # so the DB collector's SQLite queries don't block the event loop.
    _metrics_token_env = os.getenv("METRICS_TOKEN", "")

    @app.get("/metrics", include_in_schema=False, tags=["observability"])
    def metrics_endpoint(
        request: Request,
        authorization: str | None = None,
    ) -> Response:
        auth_header = request.headers.get("authorization")
        if not check_metrics_auth(auth_header):
            return Response(
                content="401 Unauthorized — supply Bearer token",
                status_code=401,
                media_type="text/plain",
            )
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # Item icons — served from local data directory
    if _ICONS_DIR.exists():
        app.mount("/icons", StaticFiles(directory=_ICONS_DIR), name="icons")

    # AA assets (backgrounds, node icons)
    if _AA_ASSETS_DIR.exists():
        app.mount("/aa-assets", StaticFiles(directory=_AA_ASSETS_DIR), name="aa-assets")

    # Spell icons
    if _SPELL_ICONS_DIR.exists():
        app.mount("/spell-icons", StaticFiles(directory=_SPELL_ICONS_DIR), name="spell-icons")

    # Serve the React build in production
    if _FRONTEND_DIST.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=_FRONTEND_DIST / "assets"),
            name="assets",
        )

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str) -> FileResponse:
            """Catch-all: serve real files from the build root if they exist
            (favicon.svg, favicon.ico, robots.txt, og-image.png, etc.) and fall
            back to index.html so React Router can handle in-app navigation."""
            # Don't swallow unmatched /api/* paths — let FastAPI return a real
            # 404 JSON response so typos surface as errors instead of HTML.
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not Found")
            if full_path:
                candidate = _FRONTEND_DIST / full_path
                # Resolve to defeat path-traversal (../../etc/passwd) and ensure
                # the resolved path stays within _FRONTEND_DIST.
                try:
                    resolved = candidate.resolve()
                    resolved.relative_to(_FRONTEND_DIST.resolve())
                except (ValueError, OSError):
                    resolved = None
                if resolved is not None and resolved.is_file():
                    return FileResponse(resolved)
            return FileResponse(_FRONTEND_DIST / "index.html")

    return app


app = create_app()
