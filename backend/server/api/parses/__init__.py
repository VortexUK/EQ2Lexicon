"""Route package — split from the original 1687-line web/routes/parses.py.

Public API: the module exposes ``router`` (a single FastAPI APIRouter)
plus the Pydantic models other modules consume. Sub-modules:

  - models       — Pydantic models (responses + ingest payloads)
  - ingest       — POST /parses/ingest + HMAC validation + snapshot helpers
  - list         — GET /parses + GET /parses/{id}
  - delete       — DELETE /parses, DELETE /parses/{id}, DELETE /parses/batch

The router itself is assembled here so external `app.include_router(parses_router)`
calls keep working unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["parses"])

# Sub-module imports must come AFTER `router` is defined — each sub-module
# adds its handlers to this router instance.
from backend.server.api.parses import delete as _delete  # noqa: E402,F401
from backend.server.api.parses import ingest as _ingest  # noqa: E402,F401
from backend.server.api.parses import list as _list  # noqa: E402,F401
from backend.server.api.parses import tamper_report as _tamper_report  # noqa: E402,F401

# Re-export the SQL helper + fight-grouping function used cross-module by
# web/routes/rankings.py to compute primary-boss kills. Pre-split these
# lived as private symbols in the monolithic parses.py and were imported
# directly — preserve that import surface so consumers don't break.
from backend.server.api.parses.list import (  # noqa: E402
    _PLAYER_COUNT_SQL,
    _group_into_fights,
)

# Re-export the models so existing `from web.routes.parses import IngestRequest`
# imports keep working.
from backend.server.api.parses.models import (  # noqa: E402
    AttackSummary,
    CombatantSummary,
    CureSummary,
    DamageTypeBreakdown,
    DeleteParsesResponse,
    HealSummary,
    IngestEncounter,
    IngestRequest,
    IngestResponse,
    ParseDetailResponse,
    ParseEncounterSummary,
    ParsePermissions,
    ParsesListResponse,
    ParseUploadSummary,
    TamperReportResponse,
    ThreatSummary,
)

__all__ = [
    "router",
    "AttackSummary",
    "CombatantSummary",
    "CureSummary",
    "DamageTypeBreakdown",
    "DeleteParsesResponse",
    "HealSummary",
    "IngestEncounter",
    "IngestRequest",
    "IngestResponse",
    "ParseDetailResponse",
    "ParseEncounterSummary",
    "ParsePermissions",
    "ParsesListResponse",
    "ParseUploadSummary",
    "TamperReportResponse",
    "ThreatSummary",
    "_PLAYER_COUNT_SQL",
    "_group_into_fights",
]
