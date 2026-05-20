from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from census.client import CensusClient
from web.db import get_active_claims, set_primary, submit_claim, withdraw_claim

router = APIRouter(tags=["claim"])

_SERVICE_ID = os.getenv("CENSUS_SERVICE_ID", "example")
_WORLD = os.getenv("EQ2_WORLD", "Varsoon")


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _require_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ClaimResponse(BaseModel):
    id: int
    discord_id: str
    character_name: str
    status: str
    requested_at: int
    reviewed_at: int | None = None
    note: str | None = None
    is_primary: int = 0


class ClaimsResponse(BaseModel):
    """All active claims for the current user."""
    approved: list[ClaimResponse]
    pending: ClaimResponse | None = None


class SubmitClaimRequest(BaseModel):
    character_name: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/claim/me", response_model=ClaimsResponse)
async def get_my_claims(request: Request) -> ClaimsResponse:
    """Return all approved characters and any pending claim for the current user."""
    user = _require_user(request)
    data = await get_active_claims(user["id"])
    return ClaimsResponse(
        approved=[ClaimResponse(**c) for c in data["approved"]],
        pending=ClaimResponse(**data["pending"]) if data["pending"] else None,
    )


@router.post("/claim", response_model=ClaimResponse, status_code=201)
async def create_claim(body: SubmitClaimRequest, request: Request) -> ClaimResponse:
    """
    Submit a claim for an additional character.
    Validates the character exists on the configured world via Census.
    Any existing pending claim is automatically cancelled (one pending at a time).
    Already-approved characters are not affected.
    """
    user = _require_user(request)
    name = body.character_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Character name is required")

    client = CensusClient(service_id=_SERVICE_ID)
    try:
        char = await client.get_character(name, _WORLD)
    finally:
        await client.close()

    if char is None:
        raise HTTPException(
            status_code=404,
            detail=f"Character '{name}' not found on {_WORLD}. "
                   f"Check the spelling — names are case-sensitive.",
        )

    try:
        claim = await submit_claim(user["id"], char.name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return ClaimResponse(**claim)


@router.delete("/claim/{claim_id}", status_code=200)
async def remove_claim(claim_id: int, request: Request) -> dict:
    """Remove a specific approved character or cancel a specific pending claim."""
    user = _require_user(request)
    if not await withdraw_claim(claim_id, user["id"]):
        raise HTTPException(status_code=404, detail="Claim not found or already inactive")
    return {"ok": True}


@router.post("/claim/{claim_id}/set-primary", status_code=200)
async def set_primary_claim(claim_id: int, request: Request) -> dict:
    """Set the specified approved character as the user's primary. No admin approval needed."""
    user = _require_user(request)
    if not await set_primary(user["id"], claim_id):
        raise HTTPException(status_code=404, detail="Claim not found, not approved, or not yours")
    return {"ok": True}
