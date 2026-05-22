from __future__ import annotations

import os
import threading
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from starlette.middleware.sessions import SessionMiddleware

from web.routes.health import router as health_router
from web.routes.auth import router as auth_router
from web.routes.character import router as character_router
from web.routes.item import router as item_router
from web.routes.claim import router as claim_router
from web.routes.admin import router as admin_router
from web.routes.guild import router as guild_router
from web.routes.characters import router as characters_router
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

_FRONTEND_DIST  = Path(__file__).resolve().parent.parent / "frontend" / "dist"
_ICONS_DIR      = Path(__file__).resolve().parent.parent / "data" / "items" / "icons"
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

    app = FastAPI(
        on_startup=[_startup],
        title="EQ2 TLE Companion",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

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
    app.include_router(characters_router, prefix="/api")

    # Item icons — served from local data directory
    if _ICONS_DIR.exists():
        app.mount("/icons", StaticFiles(directory=_ICONS_DIR), name="icons")

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
