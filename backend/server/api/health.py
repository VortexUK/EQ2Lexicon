from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend.server.config import LAUNCH_DT_ISO, SERVER_MAX_LEVEL, WORLD

_log = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

_GEAR_RATING_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "gear_rating.json"

_GEAR_RATING_DEFAULTS: dict[str, Any] = {
    "bands": [{"label": "A", "min_below_max": 4}, {"label": "B", "min_below_max": 10}],
    "fallback_band": "C",
    "matrix": {
        "fabled": {"A": "A", "B": "B", "C": "E"},
        "legendary": {"A": "B", "B": "C", "C": "F"},
        "treasured": {"A": "D", "B": "E", "C": "F"},
    },
    "grade_scores": {"A": 10, "B": 8, "C": 6, "D": 4, "E": 2, "F": 0},
    "raid_ready_min_avg": 5.5,
}


def _load_gear_rating() -> dict[str, Any]:
    """Read + parse the gear-rating config once at module import.

    The file is static reference data — it doesn't change at runtime, so
    re-reading on every /api/config hit (every page load) was pointless I/O.
    A future hot-reload toggle would re-run this helper; today it's
    module-load-only.
    """
    try:
        raw = json.loads(_GEAR_RATING_PATH.read_text(encoding="utf-8"))
        raw.pop("_comment", None)
        return raw
    except Exception as exc:
        _log.warning("[health] Failed to load gear-rating config, using defaults: %s", exc)
        return _GEAR_RATING_DEFAULTS


# Cached at module import — config file is reference data; rebuild requires
# a process restart (Railway redeploys on push, so this is fine).
_GEAR_RATING_CACHED: dict[str, Any] = _load_gear_rating()


class HealthResponse(BaseModel):
    status: str
    version: str


class ConfigResponse(BaseModel):
    server_max_level: int
    world: str
    gear_rating: dict[str, Any]
    launch_dt: str | None  # ISO-8601 UTC; null means no countdown to show


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness check — used by Railway and uptime monitors."""
    return HealthResponse(status="ok", version="0.1.0")


@router.get("/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    """Public server configuration used by the frontend."""
    return ConfigResponse(
        server_max_level=SERVER_MAX_LEVEL,
        world=WORLD,
        gear_rating=_GEAR_RATING_CACHED,
        launch_dt=LAUNCH_DT_ISO or None,
    )
