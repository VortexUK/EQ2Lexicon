from __future__ import annotations

import os
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

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
_SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-secret-change-in-production")


def create_app() -> FastAPI:
    app = FastAPI(
        title="EQ2 TLE Companion",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # Sessions must be added before CORS so the cookie is available everywhere
    app.add_middleware(SessionMiddleware, secret_key=_SESSION_SECRET, https_only=False)

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
