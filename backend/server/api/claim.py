from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.core.log_safety import scrub as _safe_for_log
from backend.server.auth_deps import require_user_session as _require_user
from backend.server.cache import character_cache, claim_cache
from backend.server.core.census_lifecycle import shared_census_client
from backend.server.db import get_active_claims, set_primary, submit_claim, upsert_user, withdraw_claim
from backend.server.limiter import limiter
from backend.server.server_context import current_world

_log = logging.getLogger(__name__)

router = APIRouter(tags=["claim"])


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


# Cache key for per-(user, world) claim data. Including ``world`` is
# essential since the per-server-URL split — a request to
# varsoon.eq2lexicon.com and one to wuoshi.eq2lexicon.com from the same
# Discord user need separate cache slots, otherwise whichever subdomain
# loads first wins and the other server shows the wrong character list
# (or nothing at all) until the 5-minute TTL expires.
def _claim_cache_key(discord_id: str, world: str) -> str:
    return f"claims:{discord_id}:{world}"


async def _build_claims_response(discord_id: str, world: str) -> tuple[ClaimsResponse, bool]:
    """
    Fetch claim + guild data from DB/Census for a specific world.
    Returns (response, cacheable) — cacheable is False if any Census guild
    fetch failed, meaning the result should not be stored (retry next request).

    Guild names are sourced from character_cache when available (no Census call
    needed).  Census is only called for characters not already in cache.

    ``world`` is passed explicitly rather than read from current_world() so
    background tasks (which may run outside any request context) get the
    right value without relying on ContextVar propagation across
    asyncio.create_task boundaries.
    """
    data = await get_active_claims(discord_id, world=world)
    approved_raw = data["approved"]

    world_lower = world.lower()

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
        async with shared_census_client() as client:
            # return_exceptions=True so a Census timeout/error comes back as an
            # Exception instance rather than propagating and losing all results
            results = await asyncio.gather(
                *[client.get_character_guild_name(n, world) for n in need_census],
                return_exceptions=True,
            )
        census_guild = dict(zip(need_census, results))
        failed_names = [n for n, gn in census_guild.items() if isinstance(gn, BaseException)]
        if failed_names:
            any_failed = True
            _log.warning(
                "[claims] Guild fetch failed for %d names (first: %s) — result will not be cached",
                len(failed_names),
                failed_names[0],
            )

    # Merge cache + Census results back in original order
    approved = []
    for c in approved_raw:
        char_name = c["character_name"]
        if char_name in cached_guild:
            gn: str | None | BaseException = cached_guild[char_name]
        else:
            gn = census_guild.get(char_name)
        approved.append(ClaimResponse(**{**c, "guild_name": gn if isinstance(gn, str) else None}))

    result = ClaimsResponse(
        approved=approved,
        pending=ClaimResponse(**data["pending"]) if data["pending"] else None,
    )
    return result, not any_failed


async def _refresh_claim_cache(discord_id: str, world: str) -> None:
    """Background task: silently rebuild the per-(user, world) claim cache."""
    try:
        result, cacheable = await _build_claims_response(discord_id, world)
        if cacheable:
            claim_cache.set(_claim_cache_key(discord_id, world), result)
        else:
            _log.warning(
                "[cache] Background claim refresh for %s on %s: some fetches failed, skipping cache update",
                _safe_for_log(discord_id),
                _safe_for_log(world),
            )
    except Exception:
        _log.exception(
            "[cache] Background claim refresh failed for %s on %s",
            _safe_for_log(discord_id),
            _safe_for_log(world),
        )


def invalidate_user_claim_cache_all_worlds(discord_id: str) -> None:
    """Bust every per-world claim cache slot for this user and schedule a
    background refresh for each registered world.

    Admin handlers that approve/reject/delete claims (or kick users) run
    outside any per-subdomain context, so there's no current_world() to
    narrow on — and we wouldn't want to anyway: a Varsoon-claim approval
    shouldn't leave the user's Wuoshi cache stale. Iterating the server
    registry covers every world the deployment serves with one call.

    Exposed at module scope (not under a leading underscore) so other
    routes can import it without reaching past the privacy convention.
    """
    from backend.server.server_context import list_public_servers

    for srv in list_public_servers():
        world = srv["world"]
        claim_cache.delete(_claim_cache_key(discord_id, world))
        asyncio.create_task(_refresh_claim_cache(discord_id, world))


@router.get("/claim/me", response_model=ClaimsResponse)
async def get_my_claims(request: Request) -> ClaimsResponse:
    """
    Return all approved characters and any pending claim for the current user.
    Always responds instantly from cache.  If the cache is stale (>5 min) a
    background refresh is fired so the *next* request is also instant.
    """
    user = _require_user(request)
    world = current_world()
    cache_key = _claim_cache_key(user["id"], world)

    cached, is_stale = claim_cache.get_stale(cache_key)
    if cached is not None:
        if is_stale:
            asyncio.create_task(_refresh_claim_cache(user["id"], world))
        return cached

    # First-ever load for this (user, world) pair — fetch synchronously
    # (no cache to serve yet).
    result, cacheable = await _build_claims_response(user["id"], world)
    if cacheable:
        claim_cache.set(cache_key, result)
    return result


@router.post("/claim", response_model=ClaimResponse, status_code=201)
@limiter.limit("5/minute")
async def create_claim(request: Request, body: SubmitClaimRequest) -> ClaimResponse:
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

    from backend.server import census_health

    if census_health.is_down():
        _log.debug("[claim] Skipping live fetch — census_health=down (name=%s)", name)
        raise HTTPException(
            status_code=503,
            detail="Census is unavailable. Cannot verify character existence — try again shortly.",
        )
    try:
        async with shared_census_client() as client:
            char = await client.get_character(name, current_world())
    except Exception as exc:
        _log.warning("[claim] Census fetch failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Census is unavailable. Cannot verify character existence — try again shortly.",
        ) from exc

    if char is None:
        raise HTTPException(
            status_code=404,
            detail=f"Character '{name}' not found on {current_world()}. Check the spelling — names are case-sensitive.",
        )

    try:
        claim = await submit_claim(user["id"], char.name, world=current_world())
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    asyncio.create_task(_refresh_claim_cache(user["id"], current_world()))
    return ClaimResponse(**claim)


@router.delete("/claim/{claim_id}", status_code=200)
async def remove_claim(claim_id: int, request: Request) -> dict:
    """Remove a specific approved character or cancel a specific pending claim."""
    user = _require_user(request)
    if not await withdraw_claim(claim_id, user["id"], world=current_world()):
        raise HTTPException(status_code=404, detail="Claim not found or already inactive")
    asyncio.create_task(_refresh_claim_cache(user["id"], current_world()))
    return {"ok": True}


@router.post("/claim/{claim_id}/set-primary", status_code=200)
async def set_primary_claim(claim_id: int, request: Request) -> dict:
    """Set the specified approved character as the user's primary. No admin approval needed."""
    user = _require_user(request)
    if not await set_primary(user["id"], claim_id, world=current_world()):
        raise HTTPException(status_code=404, detail="Claim not found, not approved, or not yours")
    asyncio.create_task(_refresh_claim_cache(user["id"], current_world()))
    return {"ok": True}
