from __future__ import annotations

import os
import threading
import time
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response

load_dotenv()
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from web.routes.health import router as health_router
from web.routes.auth import router as auth_router
from web.routes.character import router as character_router, prewarm_character_cache
from web.routes.item import router as item_router
from web.routes.claim import router as claim_router
from web.routes.admin import router as admin_router
from web.routes.guild import router as guild_router
from web.routes.guild_officer import router as guild_officer_router
from web.routes.item_watch import router as item_watch_router
from web.routes.characters import router as characters_router
from web.routes.aa import router as aa_router
from web.routes.notifications import router as notifications_router
from web.metrics import (
    APP_INFO,
    CONTENT_TYPE_LATEST,
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    _register_db_collector,
    check_metrics_auth,
    generate_latest,
    should_track_path,
)
from web.config import WORLD as _WORLD
from web import db as users_db


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
    from census.db import DB_PATH as items_db_path, init_db as items_init_db

    if not items_db_path.exists():
        return  # No items DB yet — nothing to initialise

    try:
        items_init_db(items_db_path)   # creates tables/indexes if missing

        conn = sqlite3.connect(items_db_path)
        stat_count  = conn.execute("SELECT COUNT(*) FROM item_stats").fetchone()[0]
        item_count  = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        conn.close()

        if stat_count == 0 and item_count > 0:
            print(
                f"[startup] item_stats is empty ({item_count:,} items) — "
                "running background backfill…",
                flush=True,
            )
            # Ensure repo root is on sys.path so the scripts package is importable
            import sys
            repo_root = str(Path(__file__).resolve().parent.parent)
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from scripts.backfill_item_stats import run as _backfill  # type: ignore[import]
            _backfill(rebuild=False)
            print("[startup] item_stats backfill complete.", flush=True)

    except Exception as exc:
        print(f"[startup] item_stats init/backfill error: {exc}", flush=True)

# ---------------------------------------------------------------------------
# HTTP metrics middleware
# ---------------------------------------------------------------------------

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

        return response


# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

_FRONTEND_DIST  = Path(__file__).resolve().parent.parent / "frontend" / "dist"
_ICONS_DIR           = Path(__file__).resolve().parent.parent / "data" / "items" / "icons"
_AA_ASSETS_DIR       = Path(__file__).resolve().parent.parent / "data" / "AAs"
_SPELL_ICONS_DIR     = Path(__file__).resolve().parent.parent / "data" / "spells" / "icons"
_SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-secret-change-in-production")


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

    def _init_metrics() -> None:
        # Register the DB gauge collector and set static app info.
        _register_db_collector()
        APP_INFO.info({"world": _WORLD, "version": "0.1.0"})

    app = FastAPI(
        on_startup=[_startup, _prewarm, _init_metrics],
        title="EQ2 TLE Companion",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # Metrics middleware first so it sees every request (added last = outermost)
    app.add_middleware(_MetricsMiddleware)

    # Sessions must be added before CORS so the cookie is available everywhere
    app.add_middleware(SessionMiddleware, secret_key=session_secret or _SESSION_SECRET, https_only=False)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],  # Vite dev server
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routers
    app.include_router(health_router, prefix="/api")
    app.include_router(auth_router, prefix="/api")
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
            """Catch-all: serve index.html so React Router handles navigation."""
            return FileResponse(_FRONTEND_DIST / "index.html")

    return app


app = create_app()
