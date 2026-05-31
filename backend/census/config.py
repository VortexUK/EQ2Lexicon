"""
Centralised runtime configuration read from environment variables.

All Census API consumers (web routes, bot cogs, scripts) should import
SERVICE_ID and WORLD from here rather than calling os.getenv directly,
so there is a single place to change defaults and environment variable names.

load_dotenv() is called here so that scripts don't need to worry about import
order — importing this module is sufficient to get .env values.
"""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv()  # no-op if env vars already set; safe to call multiple times
except ImportError:
    pass  # dotenv not installed (e.g. Railway production) — fine

SERVICE_ID: str = os.getenv("CENSUS_SERVICE_ID", "example")
WORLD: str = os.getenv("EQ2_WORLD", "Varsoon")
SERVER_MAX_LEVEL: int = int(os.getenv("SERVER_MAX_LEVEL", "50"))

# EQ2 servers the site accepts parse uploads from. The ACT plugin reads
# this set from /api/auth/whoami and renders it in its settings UI; the
# ingest endpoint enforces membership server-side (strict mode — uploads
# from anywhere else are rejected, including the case where the plugin
# omitted logger_server entirely, which historically fell back to WORLD).
#
# Comma-separated env var, case-insensitive matching at the comparison
# site. Default is the two active English-language EQ2 TLE servers as of
# 2026 so a fresh deploy works without manual config.
ALLOWED_SERVERS: frozenset[str] = frozenset(
    s.strip() for s in os.getenv("ALLOWED_SERVERS", "Varsoon,Wuoshi").split(",") if s.strip()
)

# ISO-8601 UTC datetime for the server launch countdown.
# Set LAUNCH_DT env var to override (e.g. "2027-03-15T18:00:00Z").
# Empty default — the frontend treats empty/past dates as "hide the countdown".
# A hardcoded past date would silently surface as a stale countdown widget for
# any contributor running without LAUNCH_DT set.
LAUNCH_DT_ISO: str = os.getenv("LAUNCH_DT", "")

# Base URL for the Daybreak Census API. Used by census/client.py; also
# imported by web/census_health.py for the health-check poll.
CENSUS_BASE_URL: str = "https://census.daybreakgames.com"

# Comma-separated Discord guild IDs that receive instant slash-command syncs.
# The bot also does a global sync, but guild syncs propagate immediately.
# Set DISCORD_SYNC_GUILD_IDS in your .env or Railway env vars.
_raw_sync_ids = os.getenv("DISCORD_SYNC_GUILD_IDS", "")
DISCORD_SYNC_GUILD_IDS: list[int] = [int(x.strip()) for x in _raw_sync_ids.split(",") if x.strip().isdigit()]
