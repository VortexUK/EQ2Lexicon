from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from parses import db as parses_db
from web.auth_deps import KNOWN_ROLES
from web.auth_deps import require_admin as _require_admin
from web.cache import claim_cache
from web.db import (
    delete_claim,
    delete_claims_for_user,
    get_claim_by_id,
    grant_role,
    list_all_users,
    list_claims,
    list_role_assignments,
    review_claim,
    revoke_role,
    set_user_access,
)
from web.routes.claim import _refresh_claim_cache

_log = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ClaimDetail(BaseModel):
    id: int
    discord_id: str
    discord_name: str | None = None  # NULL when user row missing (LEFT JOIN)
    discord_username: str | None = None
    avatar: str | None = None
    character_name: str
    status: str
    requested_at: int
    reviewed_at: int | None = None
    reviewed_by: str | None = None
    note: str | None = None


class RejectRequest(BaseModel):
    note: str | None = None


class UserItem(BaseModel):
    discord_id: str
    discord_name: str | None = None
    discord_username: str | None = None
    avatar: str | None = None
    first_seen: int
    last_seen: int
    access_status: str
    claim_count: int = 0
    # DB-granted roles (e.g. 'contributor'). Doesn't include 'admin' (env-
    # driven) or 'officer' (dynamic). Joined in via list_role_assignments so
    # this stays a single round-trip to /admin/users.
    roles: list[str] = []


class AdminParseItem(BaseModel):
    id: int
    title: str
    zone: str | None = None
    guild_name: str | None = None
    uploaded_by: str | None = None
    started_at: int
    duration_s: int
    success_level: int
    player_count: int
    hidden: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/admin/claims", response_model=list[ClaimDetail])
async def list_all_claims(
    request: Request,
    status: Literal["pending", "approved", "rejected"] | None = None,
) -> list[ClaimDetail]:
    """
    List character claims, optionally filtered by status.
    Pending claims are sorted oldest-first (queue order).
    """
    _require_admin(request)
    claims = await list_claims(status=status)
    return [ClaimDetail(**c) for c in claims]


@router.post("/admin/claims/{claim_id}/approve", response_model=ClaimDetail)
async def approve_claim(claim_id: int, request: Request) -> ClaimDetail:
    """Approve a pending claim.  Supersedes any existing approved claim for the user."""
    admin = _require_admin(request)
    result = await review_claim(claim_id, "approved", admin["id"])
    if not result:
        raise HTTPException(status_code=404, detail="Claim not found")
    claim_cache.delete(f"claims:{result['discord_id']}")
    asyncio.create_task(_refresh_claim_cache(result["discord_id"]))
    return ClaimDetail(**result)


@router.delete("/admin/claims/{claim_id}", status_code=200)
async def remove_claim(claim_id: int, request: Request) -> dict:
    """Permanently delete a claim record."""
    _require_admin(request)
    claim = await get_claim_by_id(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    await delete_claim(claim_id)
    claim_cache.delete(f"claims:{claim['discord_id']}")
    asyncio.create_task(_refresh_claim_cache(claim["discord_id"]))
    return {"ok": True}


@router.post("/admin/claims/{claim_id}/reject", response_model=ClaimDetail)
async def reject_claim(
    claim_id: int,
    body: RejectRequest,
    request: Request,
) -> ClaimDetail:
    """Reject a pending claim, optionally with a note explaining why."""
    admin = _require_admin(request)
    result = await review_claim(claim_id, "rejected", admin["id"], note=body.note)
    if not result:
        raise HTTPException(status_code=404, detail="Claim not found")
    claim_cache.delete(f"claims:{result['discord_id']}")
    asyncio.create_task(_refresh_claim_cache(result["discord_id"]))
    return ClaimDetail(**result)


@router.delete("/admin/users/{discord_id}/claims", status_code=200)
async def remove_all_user_claims(discord_id: str, request: Request) -> dict:
    """Permanently delete every claim record for a user."""
    _require_admin(request)
    count = await delete_claims_for_user(discord_id)
    claim_cache.delete(f"claims:{discord_id}")
    asyncio.create_task(_refresh_claim_cache(discord_id))
    return {"ok": True, "deleted": count}


@router.get("/admin/users", response_model=list[UserItem])
async def list_users(request: Request) -> list[UserItem]:
    """List all users with access status, claim counts, and DB-granted roles.
    Admin only."""
    _require_admin(request)
    rows = await list_all_users()
    role_map = await list_role_assignments()
    return [UserItem(**r, roles=role_map.get(r["discord_id"], [])) for r in rows]


# ---------------------------------------------------------------------------
# Role management
# ---------------------------------------------------------------------------
#
# TODO(future): self-service role requests. Admin-initiated only for now —
# see the matching TODO in web/db.py's user_roles schema for the proposed
# shape (a role_requests queue table mirroring character_claims).


@router.post("/admin/users/{discord_id}/roles/{role}", status_code=200)
async def grant_user_role(discord_id: str, role: str, request: Request) -> dict:
    """Grant a role to a user. Rejects unknown role names (typo guard).
    Idempotent — re-granting an existing role returns ok=True, granted=False."""
    admin = _require_admin(request)
    if role not in KNOWN_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown role {role!r}. Known roles: {sorted(KNOWN_ROLES)}",
        )
    inserted = await grant_role(discord_id, role, admin["id"])
    return {"ok": True, "granted": inserted}


@router.delete("/admin/users/{discord_id}/roles/{role}", status_code=200)
async def revoke_user_role(discord_id: str, role: str, request: Request) -> dict:
    """Revoke a role from a user. 404 when the user didn't have the role."""
    _require_admin(request)
    if role not in KNOWN_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown role {role!r}. Known roles: {sorted(KNOWN_ROLES)}",
        )
    removed = await revoke_role(discord_id, role)
    if not removed:
        raise HTTPException(status_code=404, detail="User does not have this role")
    return {"ok": True}


@router.get("/admin/parses", response_model=list[AdminParseItem])
async def list_parses_admin(
    request: Request,
    search: str | None = None,
    limit: int = 200,
) -> list[AdminParseItem]:
    """All parse encounters (including hidden/soft-deleted) for the sanitize
    view. Admin only. Hard-purge uses the existing
    DELETE /api/parses/{id}?purge=1 and /api/parses/batch?ids=...&purge=1."""
    _require_admin(request)
    limit = max(1, min(limit, 1000))

    def _query() -> list[dict]:
        if not parses_db.DB_PATH.exists():
            return []
        conn = parses_db.init_db(parses_db.DB_PATH)
        try:
            return parses_db.list_encounters_for_admin(conn, search=search, limit=limit)
        finally:
            conn.close()

    rows = await asyncio.get_event_loop().run_in_executor(None, _query)
    return [
        AdminParseItem(
            id=r["id"],
            title=r["title"],
            zone=r["zone"],
            guild_name=r["guild_name"],
            uploaded_by=r["uploaded_by"],
            started_at=r["started_at"],
            duration_s=r["duration_s"],
            success_level=r["success_level"],
            player_count=r["player_count"],
            hidden=bool(r["hidden_at"]),
        )
        for r in rows
    ]


@router.post("/admin/users/{discord_id}/kick", status_code=200)
async def kick_user(discord_id: str, request: Request) -> dict:
    """
    Deny a user's access and permanently delete all their claims.
    Use this to fully remove a user's presence from the system.
    Admin cannot kick themselves.
    """
    admin = _require_admin(request)
    if discord_id == admin["id"]:
        raise HTTPException(status_code=400, detail="You cannot kick yourself")
    if not await set_user_access(discord_id, "denied"):
        raise HTTPException(status_code=404, detail="User not found")
    count = await delete_claims_for_user(discord_id)
    claim_cache.delete(f"claims:{discord_id}")
    asyncio.create_task(_refresh_claim_cache(discord_id))
    return {"ok": True, "claims_deleted": count}
