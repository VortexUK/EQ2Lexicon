"""Web-app-specific configuration.

Bot-only / shared config lives in census/config.py. This module owns config
that ONLY the web layer cares about — session cookies, CORS, anything that
doesn't make sense for the Discord bot process.
"""

from __future__ import annotations

import os

from backend.census.config import (  # re-exported for backward compat — web routes use these constants  # noqa: F401
    ALLOWED_SERVERS,
    DISCORD_SYNC_GUILD_IDS,
    LAUNCH_DT_ISO,
    SERVER_MAX_LEVEL,
    SERVICE_ID,
    WORLD,
)

# Comma-separated list of origins for CORS. In production set CORS_ORIGINS
# to your actual frontend domain (e.g. "https://varsoon.eq2lexicon.com").
CORS_ORIGINS: list[str] = [
    o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
]

# Parent domain for the session cookie so one login spans both subdomains
# (e.g. ".eq2lexicon.com" in prod). Leave unset in dev (host-only cookie).
SESSION_COOKIE_DOMAIN: str | None = os.getenv("SESSION_COOKIE_DOMAIN") or None
