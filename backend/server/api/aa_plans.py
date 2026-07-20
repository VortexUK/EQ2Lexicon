"""AA planner saved builds — CRUD + read-only share.

Plans are private to their owner (list/update/delete are discord_id-scoped
in SQL, not just in the route) and pinned to the character they were planned
from. Every plan carries a share slug minted at creation; any logged-in user
holding the link can read it. The server validates allocations
STRUCTURALLY (shape, ints, bounded size) — rule legality (line thresholds,
caps, parent ranks) is the frontend engine's job and is re-checked on
render, so a hand-crafted illegal payload can never crash a viewer.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from backend.server.auth_deps import require_user_session as _require_user
from backend.server.core.text_moderation import contains_blocked_term, sanitize_text
from backend.server.core.validation import validate_character_name as _validate_character_name
from backend.server.db.aa_plans import store as aa_plans
from backend.server.limiter import limiter
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter()

MAX_PLANS_PER_CHARACTER = 20
MAX_PLAN_NAME_LEN = 60
# Structural bounds — far above any legal build (≤ ~12 trees × ~60 nodes).
_MAX_TREES = 32
_MAX_NODES_PER_TREE = 200
_MAX_RANK = 100


class PlanSummary(BaseModel):
    id: int
    name: str
    xpac: str | None = None
    share_slug: str
    created_at: int
    updated_at: int


class PlanDetail(PlanSummary):
    character_name: str
    world: str
    allocations: dict[str, dict[str, int]]
    is_mine: bool = False


class PlanWriteRequest(BaseModel):
    character_name: str
    name: str
    xpac: str | None = None
    allocations: dict[str, dict[str, int]]

    @field_validator("allocations")
    @classmethod
    def _bounded(cls, v: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
        if len(v) > _MAX_TREES:
            raise ValueError(f"too many trees (max {_MAX_TREES})")
        for tree_key, alloc in v.items():
            if not tree_key.isdigit():
                raise ValueError("tree ids must be numeric strings")
            if len(alloc) > _MAX_NODES_PER_TREE:
                raise ValueError(f"too many nodes in tree {tree_key} (max {_MAX_NODES_PER_TREE})")
            for node_key, rank in alloc.items():
                if not node_key.isdigit():
                    raise ValueError("node ids must be numeric strings")
                if not (0 < rank <= _MAX_RANK):
                    raise ValueError(f"rank out of range for node {node_key}")
        return v


class DeletePlanResponse(BaseModel):
    deleted: bool


def _clean_name(raw: str) -> str:
    name = sanitize_text(raw, max_len=MAX_PLAN_NAME_LEN)
    if not name:
        raise HTTPException(status_code=400, detail="Plan name must not be empty")
    if contains_blocked_term(name):
        raise HTTPException(status_code=400, detail="Plan name contains a blocked term")
    return name


def _clean_character(raw: str) -> str:
    name = _validate_character_name(raw.strip())
    if name is None:
        raise HTTPException(status_code=400, detail="Invalid character name")
    return name.capitalize()


def _row_to_detail(row: dict, viewer_id: str) -> PlanDetail:
    try:
        allocations = json.loads(row["allocations"] or "{}")
    except json.JSONDecodeError:
        allocations = {}
    return PlanDetail(
        id=row["id"],
        name=row["name"],
        xpac=row["xpac"],
        share_slug=row["share_slug"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        character_name=row["character_name"],
        world=row["world"],
        allocations=allocations,
        is_mine=row["discord_id"] == viewer_id,
    )


@router.get("/aa/plans", response_model=list[PlanSummary])
@limiter.limit("60/minute")
async def list_my_plans(request: Request, character: str) -> list[PlanSummary]:
    """The caller's saved plans for one character on the active server."""
    user = _require_user(request)
    character_name = _clean_character(character)
    rows = await aa_plans.list_plans(user["id"], current_world(), character_name)
    return [PlanSummary(**r) for r in rows]


@router.post("/aa/plans", response_model=PlanDetail)
@limiter.limit("20/minute")
async def create_plan(request: Request, body: PlanWriteRequest) -> PlanDetail:
    user = _require_user(request)
    world = current_world()
    character_name = _clean_character(body.character_name)
    name = _clean_name(body.name)
    if await aa_plans.count_plans(user["id"], world, character_name) >= MAX_PLANS_PER_CHARACTER:
        raise HTTPException(
            status_code=409,
            detail=f"Plan limit reached ({MAX_PLANS_PER_CHARACTER} per character). Delete one first.",
        )
    row = await aa_plans.create_plan(
        user["id"],
        world,
        character_name,
        name,
        body.xpac,
        json.dumps(body.allocations),
    )
    return _row_to_detail(row, user["id"])


@router.get("/aa/plans/{plan_id}", response_model=PlanDetail)
@limiter.limit("60/minute")
async def get_my_plan(request: Request, plan_id: int) -> PlanDetail:
    """Full plan payload — owner only (share links use the slug route)."""
    user = _require_user(request)
    row = await aa_plans.get_plan(plan_id)
    if row is None or row["discord_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Plan not found")
    return _row_to_detail(row, user["id"])


@router.put("/aa/plans/{plan_id}", response_model=PlanDetail)
@limiter.limit("30/minute")
async def update_plan(request: Request, plan_id: int, body: PlanWriteRequest) -> PlanDetail:
    user = _require_user(request)
    name = _clean_name(body.name)
    updated = await aa_plans.update_plan(
        plan_id,
        user["id"],
        name=name,
        xpac=body.xpac,
        allocations_json=json.dumps(body.allocations),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Plan not found")
    row = await aa_plans.get_plan(plan_id)
    if row is None:  # pragma: no cover — deleted between update and read
        raise HTTPException(status_code=404, detail="Plan not found")
    return _row_to_detail(row, user["id"])


@router.delete("/aa/plans/{plan_id}", response_model=DeletePlanResponse)
@limiter.limit("20/minute")
async def delete_plan(request: Request, plan_id: int) -> DeletePlanResponse:
    user = _require_user(request)
    return DeletePlanResponse(deleted=await aa_plans.delete_plan(plan_id, user["id"]))


@router.get("/aa/plan/{slug}", response_model=PlanDetail)
@limiter.limit("60/minute")
async def get_shared_plan(request: Request, slug: str) -> PlanDetail:
    """Read-only fetch by share slug — any logged-in user with the link."""
    user = _require_user(request)
    row = await aa_plans.get_plan_by_slug(slug)
    if row is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return _row_to_detail(row, user["id"])
