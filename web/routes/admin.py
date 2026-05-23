from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from web.cache import claim_cache
from web.db import delete_claim, delete_claims_for_user, get_claim_by_id, list_claims, review_claim
from web.routes.claim import _refresh_claim_cache

router = APIRouter(tags=["admin"])

_ADMIN_IDS: frozenset[str] = frozenset(
    filter(None, os.getenv("ADMIN_DISCORD_IDS", "").split(","))
)


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _require_admin(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user["id"] not in _ADMIN_IDS:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ClaimDetail(BaseModel):
    id: int
    discord_id: str
    discord_name: str | None = None   # NULL when user row missing (LEFT JOIN)
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/admin/claims", response_model=list[ClaimDetail])
async def list_all_claims(
    request: Request,
    status: str | None = None,
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
