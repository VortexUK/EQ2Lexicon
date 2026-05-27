"""
Web-layer configuration — re-exports from census.config for backward compat.
All new web routes should import from here; all bot/census code from census.config.
"""

from census.config import (  # noqa: F401
    ALLOWED_SERVERS,
    CORS_ORIGINS,
    DISCORD_SYNC_GUILD_IDS,
    LAUNCH_DT_ISO,
    SERVER_MAX_LEVEL,
    SERVICE_ID,
    WORLD,
)
