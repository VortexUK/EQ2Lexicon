"""Parses route package.

Exposes ``router`` (a single FastAPI APIRouter) plus the Pydantic models
other modules consume. Sub-modules:

  - models        — Pydantic models (responses + ingest payloads)
  - ingest        — POST /parses/ingest + HMAC validation + snapshot helpers
  - list          — GET /parses + GET /parses/{id}
  - delete        — DELETE /parses/batch, DELETE /parses/{id} (+ unhide)
  - tamper_report — plugin tamper reports
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
# backend/server/api/rankings.py to compute primary-boss kills.
from backend.server.api.parses.list import (  # noqa: E402
    _PLAYER_COUNT_SQL,
    _group_into_fights,
)

# Re-export the models so `from backend.server.api.parses import IngestRequest` works.
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
