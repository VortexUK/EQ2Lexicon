"""
GET    /api/me/role-requests        — current user's request history
POST   /api/me/role-requests        — submit a new pending request
DELETE /api/me/role-requests/{id}   — withdraw your own pending request

Self-service half of the role-request flow. The admin queue + approve/reject
endpoints live in ``web/routes/admin.py``. On approval, the admin route also
inserts a row into ``user_roles`` so the request and the grant stay decoupled
(an approved request is immutable audit history; the grant itself can be
revoked separately without rewriting the request row).
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.server.auth_deps import KNOWN_ROLES, require_user_session
from backend.server.db import (
    create_role_request,
    has_role,
    list_role_requests,
    withdraw_role_request,
)

router = APIRouter(tags=["role_requests"])


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class RoleRequestEntry(BaseModel):
    """One row from role_requests + the joined user identity columns.

    ``user_note`` is the requester's "why I want this" message; ``admin_note``
    is the reviewer's response. Either can be null."""

    id: int
    discord_id: str
    discord_name: str | None = None
    discord_username: str | None = None
    avatar: str | None = None
    role: str
    status: str  # pending / approved / rejected / withdrawn
    requested_at: int
    reviewed_at: int | None = None
    reviewed_by: str | None = None
    user_note: str | None = None
    admin_note: str | None = None


class SubmitRoleRequest(BaseModel):
    role: str = Field(..., description="Role being requested (must be in KNOWN_ROLES).")
    note: str | None = Field(
        None,
        description="Optional 'why I want this' message visible to the reviewing admin.",
        max_length=2000,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/me/role-requests", response_model=list[RoleRequestEntry])
async def list_my_role_requests(request: Request) -> list[RoleRequestEntry]:
    """All role requests this user has ever submitted, newest first.

    Resolved rows (approved/rejected/withdrawn) sit alongside any current
    pending one — the settings page filters/groups them client-side."""
    user = require_user_session(request)
    rows = await list_role_requests(discord_id=user["id"])
    return [RoleRequestEntry(**r) for r in rows]


@router.post("/me/role-requests", response_model=RoleRequestEntry, status_code=201)
async def submit_role_request(body: SubmitRoleRequest, request: Request) -> RoleRequestEntry:
    """Submit a new pending request. Rejected pre-flight if the user already
    holds the role (no point queueing) or has an existing pending request for
    it (the unique index would catch it anyway — we just return a friendlier
    error)."""
    user = require_user_session(request)
    if body.role not in KNOWN_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown role {body.role!r}. Known roles: {sorted(KNOWN_ROLES)}",
        )
    if await has_role(user["id"], body.role):
        raise HTTPException(status_code=409, detail=f"You already have the {body.role!r} role")
    try:
        new_id = await create_role_request(user["id"], body.role, body.note)
    except sqlite3.IntegrityError as exc:
        # The partial unique index on (discord_id, role) WHERE status='pending'
        # catches this — surface a clean 409 rather than a 500.
        raise HTTPException(
            status_code=409,
            detail=f"You already have a pending request for {body.role!r}",
        ) from exc

    # Re-fetch via list_ so the joined user identity columns are populated.
    rows = await list_role_requests(discord_id=user["id"])
    fresh = next((r for r in rows if r["id"] == new_id), None)
    if fresh is None:
        # Shouldn't happen — INSERT succeeded but the SELECT didn't see it.
        raise HTTPException(status_code=500, detail="Failed to load freshly-created request")
    return RoleRequestEntry(**fresh)


@router.delete("/me/role-requests/{request_id}", status_code=200)
async def withdraw_my_role_request(request_id: int, request: Request) -> dict:
    """Withdraw your own pending request. 404 if not yours or already resolved
    — the underlying helper is scoped to (id, discord_id, status='pending')
    so this is safe against cross-user withdrawal attempts."""
    user = require_user_session(request)
    ok = await withdraw_role_request(request_id, user["id"])
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Request not found, not yours, or already resolved",
        )
    return {"ok": True}
