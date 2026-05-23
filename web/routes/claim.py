from __future__ import annotations

import asyncio
import logging

_log = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from census.client import CensusClient
from web.cache import character_cache, claim_cache
from web.config import SERVICE_ID as _SERVICE_ID, WORLD as _WORLD
from web.db import get_active_claims, set_primary, submit_claim, upsert_user, withdraw_claim

router = APIRouter(tags=["claim"])


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
    guild_name: str | None = None


class ClaimsResponse(BaseModel):
    """All active claims for the current user."""
    approved: list[ClaimResponse]
    pending: ClaimResponse | None = None


class SubmitClaimRequest(BaseModel):
    character_name: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

async def _build_claims_response(discord_id: str) -> tuple[ClaimsResponse, bool]:
    """
    Fetch claim + guild data from DB/Census.
    Returns (response, cacheable) — cacheable is False if any Census guild
    fetch failed, meaning the result should not be stored (retry next request).

    Guild names are sourced from character_cache when available (no Census call
    needed).  Census is only called for characters not already in cache.
    """
    data = await get_active_claims(discord_id)
    approved_raw = data["approved"]

    world_lower = _WORLD.lower()

    # Check character_cache first — guild members loaded via the guild page
    # will already have their guild_name populated there.
    need_census: list[str] = []
    cached_guild: dict[str, str | None] = {}
    for c in approved_raw:
        char_name = c["character_name"]
        cached_char, _ = character_cache.get_stale(f"{char_name.lower()}:{world_lower}")
        if cached_char is not None:
            cached_guild[char_name] = getattr(cached_char, "guild_name", None)
        else:
            need_census.append(char_name)

    # Fire Census calls only for characters not in character_cache
    any_failed = False
    census_guild: dict[str, str | None | BaseException] = {}
    if need_census:
        client = CensusClient(service_id=_SERVICE_ID)
        try:
            # return_exceptions=True so a Census timeout/error comes back as an
            # Exception instance rather than propagating and losing all results
            results = await asyncio.gather(
                *[client.get_character_guild_name(n, _WORLD) for n in need_census],
                return_exceptions=True,
            )
        finally:
            await client.close()
        census_guild = dict(zip(need_census, results))
        failed_names = [n for n, gn in census_guild.items() if isinstance(gn, BaseException)]
        if failed_names:
            any_failed = True
            _log.warning("[Claims] Guild fetch failed for: %s — result will not be cached", failed_names)

    # Merge cache + Census results back in original order
    approved = []
    for c in approved_raw:
        char_name = c["character_name"]
        if char_name in cached_guild:
            gn: str | None | BaseException = cached_guild[char_name]
        else:
            gn = census_guild.get(char_name)
        approved.append(
            ClaimResponse(**{**c, "guild_name": gn if isinstance(gn, str) else None})
        )

    result = ClaimsResponse(
        approved=approved,
        pending=ClaimResponse(**data["pending"]) if data["pending"] else None,
    )
    return result, not any_failed


async def _refresh_claim_cache(discord_id: str) -> None:
    """Background task: silently rebuild the claim cache for a user."""
    try:
        result, cacheable = await _build_claims_response(discord_id)
        if cacheable:
            claim_cache.set(f"claims:{discord_id}", result)
        else:
            _log.warning("[Cache] Background claim refresh for %s: some fetches failed, skipping cache update", discord_id)
    except Exception as exc:
        _log.error("[Cache] Background claim refresh failed for %s: %s", discord_id, exc)


@router.get("/claim/me", response_model=ClaimsResponse)
async def get_my_claims(request: Request) -> ClaimsResponse:
    """
    Return all approved characters and any pending claim for the current user.
    Always responds instantly from cache.  If the cache is stale (>5 min) a
    background refresh is fired so the *next* request is also instant.
    """
    user = _require_user(request)
    cache_key = f"claims:{user['id']}"

    cached, is_stale = claim_cache.get_stale(cache_key)
    if cached is not None:
        if is_stale:
            asyncio.create_task(_refresh_claim_cache(user["id"]))
        return cached

    # First-ever load for this user — fetch synchronously (no cache to serve yet)
    result, cacheable = await _build_claims_response(user["id"])
    if cacheable:
        claim_cache.set(cache_key, result)
    return result


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
    if len(name) > 64:
        raise HTTPException(status_code=400, detail="Character name is too long")

    # Ensure the user row exists — it may be missing if the DB was reset while
    # the session cookie was still valid (i.e. user never re-authed after reset).
    await upsert_user(
        discord_id=user["id"],
        discord_name=user.get("global_name") or user.get("username", user["id"]),
        discord_username=user.get("username", ""),
        avatar=user.get("avatar"),
    )

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

    asyncio.create_task(_refresh_claim_cache(user["id"]))
    return ClaimResponse(**claim)


@router.delete("/claim/{claim_id}", status_code=200)
async def remove_claim(claim_id: int, request: Request) -> dict:
    """Remove a specific approved character or cancel a specific pending claim."""
    user = _require_user(request)
    if not await withdraw_claim(claim_id, user["id"]):
        raise HTTPException(status_code=404, detail="Claim not found or already inactive")
    asyncio.create_task(_refresh_claim_cache(user["id"]))
    return {"ok": True}


@router.post("/claim/{claim_id}/set-primary", status_code=200)
async def set_primary_claim(claim_id: int, request: Request) -> dict:
    """Set the specified approved character as the user's primary. No admin approval needed."""
    user = _require_user(request)
    if not await set_primary(user["id"], claim_id):
        raise HTTPException(status_code=404, detail="Claim not found, not approved, or not yours")
    asyncio.create_task(_refresh_claim_cache(user["id"]))
    return {"ok": True}
