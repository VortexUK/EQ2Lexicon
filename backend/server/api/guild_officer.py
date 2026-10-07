from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.server.api.claim import invalidate_user_claim_cache_all_worlds
from backend.server.api.guild import _leader_chars, _officer_chars, _roster_rank_map, _validate_guild_name
from backend.server.auth_deps import require_admin as _require_admin
from backend.server.core.audit_log import audit_log
from backend.server.db import (
    get_claim_by_id,
    list_claims,
    list_pending_users,
    review_claim,
    set_user_access,
)
from backend.server.limiter import limiter, user_rate_key
from backend.server.server_context import current_world

router = APIRouter(tags=["guild"])


async def _reviewable_claim(claim_id: int, guild_name: str) -> dict | None:
    """The claim an officer of ``guild_name`` may review, or None.

    Officer rights are derived from approved claims, so a claim is only in
    scope when it is still pending, belongs to the request's world and names
    a current member of THIS guild's roster. Every failure is the same None
    (→ 404) so the route never confirms that a foreign claim id exists.
    """
    claim = await get_claim_by_id(claim_id)
    if not claim or claim.get("status") != "pending":
        return None
    if claim.get("world") != current_world():
        return None
    rank_map = await _roster_rank_map(guild_name)
    if claim["character_name"].lower() not in rank_map:
        return None
    return claim


# ---------------------------------------------------------------------------
# Models — officer claim review
# ---------------------------------------------------------------------------


class GuildClaimItem(BaseModel):
    id: int
    # The claimant's Discord id — officers reviewing a claim already see the
    # claimant's Discord name, and the frontend needs the id to build the
    # avatar CDN URL (both the custom-avatar path and the default-bucket
    # fallback derive from it).
    discord_id: str
    discord_name: str
    avatar: str | None = None
    character_name: str
    requested_at: int
    is_own: bool = False  # True when this claim belongs to the requesting officer


class RejectNoteRequest(BaseModel):
    note: str | None = None


# ---------------------------------------------------------------------------
# Officer claim-review endpoints
# ---------------------------------------------------------------------------


@router.get("/guild/{guild_name}/officer-status")
async def get_officer_status(guild_name: str, request: Request) -> dict:
    """
    Return whether the current user holds an officer rank in this guild, and
    separately whether they are its LEADER (rank 0 — the one rank that may
    change guild settings). Always returns 200 (unauthenticated / non-officer
    users get both false).
    """
    _validate_guild_name(guild_name)
    user = request.session.get("user")
    if not user:
        return {"is_officer": False, "is_leader": False}
    chars = await _officer_chars(user["id"], guild_name)
    leader = await _leader_chars(user["id"], guild_name) if chars else set()
    return {"is_officer": bool(chars), "is_leader": bool(leader)}


@router.get("/guild/{guild_name}/claims", response_model=list[GuildClaimItem])
async def get_guild_claims(guild_name: str, request: Request) -> list[GuildClaimItem]:
    """
    List all pending claims for characters that are members of this guild.
    Requires the requesting user to be an officer (rank 0 or 1) of the guild.
    """
    _validate_guild_name(guild_name)
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not await _officer_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")

    rank_map = await _roster_rank_map(guild_name)
    pending = await list_claims(status="pending", world=current_world())

    return [
        GuildClaimItem(
            id=c["id"],
            discord_id=c["discord_id"],
            discord_name=c["discord_name"],
            avatar=c.get("avatar"),
            character_name=c["character_name"],
            requested_at=c["requested_at"],
            is_own=c["discord_id"] == user["id"],
        )
        for c in pending
        if c["character_name"].lower() in rank_map
    ]


@router.post("/guild/{guild_name}/claims/{claim_id}/approve", response_model=GuildClaimItem)
@limiter.limit("30/minute", key_func=user_rate_key)
async def officer_approve_claim(guild_name: str, claim_id: int, request: Request) -> GuildClaimItem:
    """Approve a pending claim.  Officers cannot approve their own claims."""
    _validate_guild_name(guild_name)
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not await _officer_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")

    claim = await _reviewable_claim(claim_id, guild_name)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim["discord_id"] == user["id"]:
        raise HTTPException(status_code=403, detail="You cannot approve your own claim")

    result = await review_claim(claim_id, "approved", user["id"])
    if not result:
        raise HTTPException(status_code=404, detail="Claim not found")
    invalidate_user_claim_cache_all_worlds(result["discord_id"])
    audit_log(
        "claim_approved",
        actor=user["id"],
        claim_id=claim_id,
        character=result["character_name"],
        discord_id=result["discord_id"],
        guild=guild_name,
        via="officer",
    )
    return GuildClaimItem(
        id=result["id"],
        discord_id=result["discord_id"],
        discord_name=result["discord_name"],
        avatar=result.get("avatar"),
        character_name=result["character_name"],
        requested_at=result["requested_at"],
        is_own=False,
    )


@router.post("/guild/{guild_name}/claims/{claim_id}/reject")
@limiter.limit("30/minute", key_func=user_rate_key)
async def officer_reject_claim(
    guild_name: str,
    claim_id: int,
    body: RejectNoteRequest,
    request: Request,
) -> dict:
    """Reject a pending claim, optionally with a note.  Officers cannot reject their own claims."""
    _validate_guild_name(guild_name)
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not await _officer_chars(user["id"], guild_name):
        raise HTTPException(status_code=403, detail="Officer access required")

    claim = await _reviewable_claim(claim_id, guild_name)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim["discord_id"] == user["id"]:
        raise HTTPException(status_code=403, detail="You cannot reject your own claim")

    result = await review_claim(claim_id, "rejected", user["id"], note=body.note)
    if not result:
        raise HTTPException(status_code=404, detail="Claim not found")
    invalidate_user_claim_cache_all_worlds(result["discord_id"])
    audit_log(
        "claim_rejected",
        actor=user["id"],
        claim_id=claim_id,
        character=result["character_name"],
        discord_id=result["discord_id"],
        guild=guild_name,
        via="officer",
        note=(body.note or "")[:80],
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin — user access approval
# ---------------------------------------------------------------------------


class PendingUserItem(BaseModel):
    discord_id: str
    discord_name: str
    discord_username: str | None = None
    avatar: str | None = None
    first_seen: int


@router.get("/admin/pending-users", response_model=list[PendingUserItem])
async def get_pending_users(request: Request) -> list[PendingUserItem]:
    """List all users awaiting access approval. Admin only."""
    _require_admin(request)
    rows = await list_pending_users()
    return [PendingUserItem(**r) for r in rows]


@router.post("/admin/users/{discord_id}/approve", status_code=200)
async def approve_user(discord_id: str, request: Request) -> dict:
    """Grant access to a pending user. Admin only."""
    _require_admin(request)
    if not await set_user_access(discord_id, "approved"):
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True}


@router.post("/admin/users/{discord_id}/deny", status_code=200)
async def deny_user(discord_id: str, request: Request) -> dict:
    """Deny access to a pending (or previously approved) user. Admin only."""
    admin = _require_admin(request)
    if discord_id == admin["id"]:
        raise HTTPException(status_code=400, detail="You cannot deny your own access")
    if not await set_user_access(discord_id, "denied"):
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True}
