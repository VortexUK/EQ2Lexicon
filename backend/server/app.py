from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response

load_dotenv()
from pathlib import Path

from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from backend.sql_loader import load_sql

_SQL = load_sql(__file__)


class _HashedAssetsStaticFiles(StaticFiles):
    """StaticFiles wrapper that stamps long-lived cache headers on every
    response. ONLY safe for content-addressed file trees (like Vite's
    hashed /assets/*) — the hash in the filename IS the cache key, so
    'immutable' is correct: any content change produces a new filename,
    busting the cache automatically.

    NEVER use this for non-hashed files — root-level index.html, favicon
    etc. need short or no caching so deploys propagate. The serve_spa
    catch-all handles those separately with no-cache headers."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from backend.server import db as users_db
from backend.server import server_context
from backend.server.api.aa import router as aa_router
from backend.server.api.act_triggers import router as act_triggers_router
from backend.server.api.admin import router as admin_router
from backend.server.api.auth import router as auth_router
from backend.server.api.auth_tokens import router as auth_tokens_router
from backend.server.api.census import router as census_router
from backend.server.api.character import prewarm_character_cache
from backend.server.api.character import router as character_router
from backend.server.api.characters import router as characters_router
from backend.server.api.claim import router as claim_router
from backend.server.api.classes import router as classes_router
from backend.server.api.guild import router as guild_router
from backend.server.api.guild_officer import router as guild_officer_router
from backend.server.api.health import router as health_router
from backend.server.api.item import router as item_router
from backend.server.api.item_watch import router as item_watch_router
from backend.server.api.notifications import router as notifications_router
from backend.server.api.parses import router as parses_router
from backend.server.api.raid_strategies import router as raid_strategies_router
from backend.server.api.rankings import router as rankings_router
from backend.server.api.recipes import router as recipes_router
from backend.server.api.role_requests import router as role_requests_router
from backend.server.api.server import router as server_router
from backend.server.api.supporters import router as supporters_router
from backend.server.api.zones import router as zones_router
from backend.server.api.zones_admin import router as zones_admin_router
from backend.server.cache import aa_cache, character_cache, claim_cache, guild_cache
from backend.server.config import CORS_ORIGINS as _CORS_ORIGINS
from backend.server.config import SESSION_COOKIE_DOMAIN as _SESSION_COOKIE_DOMAIN
from backend.server.config import WORLD as _WORLD
from backend.server.constants import CACHE_SWEEP_INTERVAL_S
from backend.server.core import census_lifecycle
from backend.server.limiter import limiter
from backend.server.metrics import (
    APP_ERRORS,
    APP_INFO,
    APP_INFO_LEGACY,
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    USER_PAGE_VIEWS,
    _register_db_collector,
    check_metrics_auth,
    should_track_path,
    should_track_user_view,
)
from backend.server.server_context import ServerContextMiddleware

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

    from backend.eq2db.items import DB_PATH as items_db_path
    from backend.eq2db.items import init_db as items_init_db

    if not items_db_path.exists():
        return  # No items DB yet — nothing to initialise

    try:
        items_init_db(items_db_path)  # creates tables/indexes if missing

        conn = sqlite3.connect(items_db_path)
        stat_count = conn.execute(_SQL["count_item_stats"]).fetchone()[0]
        item_count = conn.execute(_SQL["count_items"]).fetchone()[0]
        conn.close()

        if stat_count == 0 and item_count > 0:
            _log.info(
                "[startup] item_stats is empty (%d items) — running background backfill…",
                item_count,
            )
            # Ensure repo root is on sys.path so the scripts package is importable
            import sys

            repo_root = str(Path(__file__).resolve().parent.parent.parent)
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from scripts.backfill_item_stats import run as _backfill  # type: ignore[import]

            _backfill(rebuild=False)
            _log.info("[startup] item_stats backfill complete.")

    except Exception:
        _log.exception("[startup] item_stats init/backfill error")


def _ensure_recipe_levels() -> None:
    """
    Called in a background thread at startup.

    * Adds the recipes.out_level column if missing (idempotent migration).
    * If any recipe rows still have a NULL out_level and items.db has data,
      resolves the crafted-output level from items.db and fills it. This runs
      once after a fresh deployment or after the code is upgraded on an existing
      DB; subsequent boots find 0 NULL rows and no-op.

    out_level drives the T1–T14 craft tier on the recipe page (replacing the old
    fuel-name heuristic). Until this pass finishes, craft_tier reads blank for
    not-yet-filled rows; all other recipe search/display works immediately.
    """
    import sqlite3

    from backend.eq2db.items import DB_PATH as items_db_path
    from backend.eq2db.recipes import DB_PATH as recipes_db_path
    from backend.eq2db.recipes import init_db as recipes_init_db

    if not recipes_db_path.exists() or not items_db_path.exists():
        return  # need both DBs to resolve levels

    try:
        recipes_init_db(recipes_db_path).close()  # ensures out_level column exists

        conn = sqlite3.connect(recipes_db_path)
        missing = conn.execute("SELECT COUNT(*) FROM recipes WHERE out_level IS NULL").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
        conn.close()

        if missing > 0 and total > 0:
            _log.info(
                "[startup] %d/%d recipes missing out_level — running background backfill…",
                missing,
                total,
            )
            import sys

            repo_root = str(Path(__file__).resolve().parent.parent.parent)
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            from scripts.backfill_recipe_levels import run as _backfill  # type: ignore[import]

            _backfill(rebuild=False)
            _log.info("[startup] recipe out_level backfill complete.")

    except Exception:
        _log.exception("[startup] recipe out_level init/backfill error")


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

            # Server-error counter for the Databases dashboard. Only 5xx — 4xx
            # is mostly user error (auth, validation) and would drown out the
            # actual server-side failures we want to alert on. `source` is the
            # matched route template so spikes can be attributed to the
            # offending endpoint without exploding label cardinality.
            if response.status_code >= 500:
                APP_ERRORS.labels(source=label_path).inc()

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

_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


# Discord (and other crawlers) read server-rendered OG tags, not the SPA's JS.
# index.html ships the big "hero" embed (og-image.png + summary_large_image).
# For deep links (item/character/etc.) we serve the same HTML with the image
# swapped to the small square logo + a compact `summary` card, so a shared
# link shows a tidy thumbnail instead of the full splash. Cached per index.html
# mtime (rebuilds after a deploy; process-lifetime otherwise).
_SMALL_EMBED_CACHE: dict[float, str] = {}


def _small_embed_html(index_path: Path) -> str | None:
    """Return index.html rewritten for the compact sub-page Discord embed, or
    None if index.html can't be read."""
    try:
        mtime = index_path.stat().st_mtime
    except OSError:
        return None
    cached = _SMALL_EMBED_CACHE.get(mtime)
    if cached is not None:
        return cached
    try:
        html = index_path.read_text(encoding="utf-8")
    except OSError:
        return None
    small = (
        html.replace("og-image.png", "favicon-192.png")  # og:image + twitter:image
        .replace("summary_large_image", "summary")  # compact card
        .replace('content="1536"', 'content="192"')  # og:image:width
        .replace('content="1024"', 'content="192"')  # og:image:height
    )
    _SMALL_EMBED_CACHE.clear()  # only the current mtime's variant is ever needed
    _SMALL_EMBED_CACHE[mtime] = small
    return small


_ICONS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "items" / "icons"
_AA_ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "AAs"
_SPELL_ICONS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "spells" / "icons"
_CLASS_ICONS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "classes" / "icons"

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


# ---------------------------------------------------------------------------
# Exception handlers — surface X-Request-ID in error JSON bodies
# ---------------------------------------------------------------------------


async def _http_exception_handler(request: Request, exc: HTTPException) -> Response:
    """HTTPException → JSON body with the request_id surfaced (for API paths)
    or plain text (for everything else — most importantly static assets).

    Why the split: lazy-loaded JS chunks request URLs like
    ``/assets/ParsesPage-<hash>.js``. If that file is missing (stale CDN
    cache pointing at old hashes, etc.), Starlette raises a 404. Previously
    we returned that as ``Content-Type: application/json`` for EVERY path,
    which Firefox refused to execute as a JS module ("disallowed MIME
    type"), breaking the whole SPA. JSON is only useful for our API
    consumers; static-asset fetches just need a proper status code.
    """
    from backend.server.core.request_context import request_id_var

    rid = request_id_var.get() or "-"
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "request_id": rid},
            headers={"X-Request-ID": rid},
        )
    return Response(
        content=f"{exc.status_code} {exc.detail}",
        status_code=exc.status_code,
        media_type="text/plain",
        headers={"X-Request-ID": rid},
    )


async def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """RequestValidationError (422) → JSON body with request_id surfaced.

    Logs the failing field(s) at WARNING so 422s are diagnosable from server
    logs — the client only receives the JSON body, and a plugin may not surface
    it. Logs just loc/type/msg (NOT the raw input value, which can be large or
    carry payload data we don't want in logs).
    """
    from backend.server.core.request_context import request_id_var

    rid = request_id_var.get() or "-"
    summary = (
        "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')} [{e.get('type', '')}]"
            for e in exc.errors()
        )
        or "<no field detail>"
    )
    _log.warning("[validation] 422 %s %s (request_id=%s) — %s", request.method, request.url.path, rid, summary)
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "request_id": rid},
        headers={"X-Request-ID": rid},
    )


def create_app(session_secret: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # ---- startup (sync) ----
        # Assert the single-process assumption baked into:
        #   - web/census_events.py — SSE pub/sub uses an in-process asyncio
        #     queue; cross-worker fan-out would need Redis.
        #   - web/routes/rankings.py:_cached_zones_data — LRU is per-process;
        #     invalidate_zones_cache() only clears the LRU on THIS worker.
        #
        # Set GUNICORN_WORKERS=1 (or unset it for uvicorn's default) on the
        # deploy. If you ever need to scale workers, the SSE + LRU layers
        # need a Redis-backed rewrite before that flip is safe.
        from backend.core.logging_config import configure_logging

        configure_logging()
        _workers = int(os.getenv("WEB_CONCURRENCY", "1"))
        _log.info("[startup] WEB_CONCURRENCY=%d (must be 1 for in-process SSE + LRU)", _workers)
        if _workers != 1:
            raise RuntimeError(
                f"WEB_CONCURRENCY={_workers} is incompatible with the in-process "
                f"SSE pub/sub (web/census_events.py) and _cached_zones_data LRU "
                f"(web/routes/rankings.py). Set WEB_CONCURRENCY=1 or rewrite "
                f"both layers to use a cross-process backplane before scaling."
            )
        users_db.init_db()
        # Open-signup backlog clear: when OPEN_SIGNUP is on, auto-approve any
        # users already waiting in the pending queue so the policy applies to
        # them too (new logins are auto-approved at upsert time). Idempotent —
        # a no-op once nothing is pending.
        from backend.server.config import OPEN_SIGNUP as _OPEN_SIGNUP

        if _OPEN_SIGNUP:
            _approved = await users_db.approve_all_pending()
            _log.info("[startup] OPEN_SIGNUP on — approved %d pending user(s).", _approved)
        server_context.load_registry()
        # Initialise the parses DB too so the schema + migrations are in place
        # before the first /api/parses/ingest hits — otherwise the first
        # upload's request pays that cost on the request thread.
        from backend.server.parses import db as parses_db

        parses_db.init_db()
        # Initialise zones.db on startup too so admin-curation tables
        # (featured_raid_expansions / featured_raid_zones) exist on
        # pre-migration DBs without requiring a rebuild via the seed
        # script. init_db is CREATE TABLE IF NOT EXISTS throughout, so
        # this is safe on populated zones.db files.
        from backend.eq2db import zones as zones_db

        zones_db.init_db().close()
        # Initialise recipes.db synchronously so the out_level column (and any
        # future migrations) exist BEFORE the first recipe read. The search and
        # eq2db find_* paths SELECT out_level directly and do not run init_db
        # themselves — without this, requests arriving before the background
        # backfill thread finishes init_db hit "no such column: out_level".
        # init_db is CREATE TABLE IF NOT EXISTS + idempotent ALTERs, so it's
        # safe on a populated recipes.db. The slower data backfill still runs in
        # the background thread below.
        from backend.eq2db import recipes as recipes_db

        recipes_db.init_db().close()
        # Run the item-stats check in a background thread so it never blocks
        # startup or Railway health checks.  On a fresh deployment the backfill
        # may take ~60–90 s; stat-filter searches will return 0 results until it
        # finishes, but name/tier/slot/class/level searches work immediately.
        threading.Thread(target=_ensure_item_stats, daemon=True, name="item-stats-backfill").start()
        # Recipe craft-tier (out_level) self-heal — resolves crafted-output levels
        # from items.db so the recipe page shows correct T1–T14 tiers. Background
        # so it never blocks startup; one-time pass after a fresh deploy/upgrade.
        threading.Thread(target=_ensure_recipe_levels, daemon=True, name="recipe-levels-backfill").start()
        # Register the DB gauge collector and set static app info.
        _register_db_collector()
        APP_INFO.info({"world": _WORLD, "version": "0.1.0"})
        APP_INFO_LEGACY.info({"world": _WORLD, "version": "0.1.0"})  # legacy; drop next release

        # ---- async background tasks (tracked so shutdown can cancel) ----
        from backend.server import census_health

        tasks: list[asyncio.Task] = [
            asyncio.create_task(prewarm_character_cache(), name="prewarm-character-cache"),
            asyncio.create_task(_cache_sweep_loop(), name="cache-sweep-loop"),
            asyncio.create_task(census_health.poll_loop(), name="census-health-poll"),
        ]

        try:
            yield
        finally:
            # ---- shutdown ----
            for task in tasks:
                task.cancel()
            # Collect cancellation acknowledgements; swallow CancelledError
            # because that's exactly what we asked for.
            await asyncio.gather(*tasks, return_exceptions=True)
            # Close the shared aiohttp session(s) so the process exits
            # without aiohttp's "Unclosed client session" warning.
            await census_lifecycle.aclose_all()

    async def _cache_sweep_loop() -> None:
        """Periodically evict max_age-expired entries from all caches.

        CancelledError propagates out of asyncio.sleep — letting it bubble
        gives the lifespan cleanup deterministic shutdown. No try/except
        around the sleep.
        """
        while True:
            await asyncio.sleep(CACHE_SWEEP_INTERVAL_S)
            for cache in (character_cache, guild_cache, claim_cache, aa_cache):
                cache.sweep()

    app = FastAPI(
        lifespan=lifespan,
        title="EQ2 Lexicon",
        version="0.1.0",
        docs_url="/api/docs" if _SHOW_DOCS else None,
        redoc_url="/api/redoc" if _SHOW_DOCS else None,
        openapi_url="/api/openapi.json" if _SHOW_DOCS else None,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    # Surface X-Request-ID in 4xx/5xx JSON so users can quote it back to support.
    # Register for BOTH FastAPI's HTTPException and Starlette's parent
    # HTTPException. Unmatched-route 404s raise the Starlette parent directly;
    # only registering for the FastAPI subclass fails to catch them. (Passes
    # locally on Windows but failed in CI on Linux — Starlette/FastAPI version
    # interaction.)
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app.add_exception_handler(HTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)  # type: ignore[arg-type]

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
        domain=_SESSION_COOKIE_DOMAIN,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(ServerContextMiddleware)

    # RequestContextMiddleware: mints UUID4 request_id, sets contextvars,
    # echoes X-Request-ID on responses. Install between ServerContextMiddleware
    # and SessionMiddleware in add_middleware order — Starlette executes
    # add_middleware calls in reverse, so this runs AFTER SessionMiddleware
    # (request.session["user"] is available) and BEFORE ServerContextMiddleware
    # (request_id is set before ServerContextMiddleware fires its logs).
    from backend.server.core.request_context_middleware import RequestContextMiddleware

    app.add_middleware(RequestContextMiddleware)

    # API routers — one entry per router, registered at /api prefix
    _ROUTERS = [
        health_router,
        auth_router,
        auth_tokens_router,
        character_router,
        item_router,
        claim_router,
        admin_router,
        guild_router,
        guild_officer_router,
        item_watch_router,
        characters_router,
        aa_router,
        notifications_router,
        recipes_router,
        parses_router,
        rankings_router,
        classes_router,
        zones_router,
        zones_admin_router,
        raid_strategies_router,
        act_triggers_router,
        role_requests_router,
        census_router,
        server_router,
        supporters_router,
    ]
    for _r in _ROUTERS:
        app.include_router(_r, prefix="/api")

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

    # Static data directories (icons, assets) — data-driven mount table
    _STATIC_MOUNTS: list[tuple[str, Path]] = [
        ("/icons", _ICONS_DIR),
        ("/aa-assets", _AA_ASSETS_DIR),
        ("/spell-icons", _SPELL_ICONS_DIR),
        ("/class-icons", _CLASS_ICONS_DIR),
    ]
    for _mount_path, _dir_path in _STATIC_MOUNTS:
        if _dir_path.exists():
            app.mount(_mount_path, StaticFiles(directory=_dir_path), name=_mount_path.lstrip("/"))

    # Serve the React build in production. If the path doesn't exist, log loud
    # — silently skipping is what hid the 2026-05-31 reorg outage (the SPA
    # mount + catch-all just never registered and every non-API URL 404'd
    # with nothing in the logs to point at it).
    if not _FRONTEND_DIST.exists():
        _log.error(
            "[startup] FRONTEND DIST NOT FOUND at %s — SPA + catch-all routes are NOT mounted, "
            "every non-API URL will 404. This is almost always a path bug after a reorg.",
            _FRONTEND_DIST,
        )
    else:
        app.mount(
            "/assets",
            _HashedAssetsStaticFiles(directory=_FRONTEND_DIST / "assets"),
            name="assets",
        )

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str) -> Response:
            """Catch-all: serve real files from the build root if they exist
            (favicon.svg, favicon.ico, robots.txt, og-image.png, etc.) and fall
            back to index.html so React Router can handle in-app navigation.

            Cache-Control: no-cache, must-revalidate on everything served here.
            Hashed chunks live under /assets and are handled by the
            _HashedAssetsStaticFiles mount above (cache-forever via the hash).
            Index.html and root-level non-hashed files MUST re-validate so a
            deploy's new chunk hash references are picked up — otherwise the
            browser's cached index.html points at chunks that no longer exist
            and the SPA breaks until hard-refresh (the 2026-05-31 incident)."""
            # Don't swallow unmatched /api/* paths — let FastAPI return a real
            # 404 JSON response so typos surface as errors instead of HTML.
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not Found")
            target: Path = _FRONTEND_DIST / "index.html"
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
                    target = resolved
            # SPA fallback for a deep link (non-root, not a real file): serve the
            # compact-embed HTML so Discord shows the small logo instead of the
            # hero. Root ("") and real static files keep their normal response.
            if target == _FRONTEND_DIST / "index.html" and full_path:
                small = _small_embed_html(target)
                if small is not None:
                    return HTMLResponse(
                        small,
                        headers={"Cache-Control": "no-cache, must-revalidate"},
                    )
            return FileResponse(
                target,
                headers={"Cache-Control": "no-cache, must-revalidate"},
            )

    return app


app = create_app()
