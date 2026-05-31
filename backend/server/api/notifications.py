"""
GET /api/notifications

Returns counts of items needing attention for the current user:
  - pending_claims : character claims the user can action as an officer
  - pending_users  : users awaiting access approval (admin only)
  - officer_guild  : guild name to navigate to for claim review (if applicable)

Designed to be polled cheaply every 60 s from the frontend.
All heavy lookups go through the existing caches so Census is never
hit on every poll.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel
from starlette.requests import Request

from backend.server.api.guild import _OFFICER_RANKS, _roster_rank_map
from backend.server.auth_deps import ADMIN_IDS as _ADMIN_IDS  # canonical source
from backend.server.cache import character_cache
from backend.server.core.cache_keys import char_cache_key
from backend.server.db import get_active_claims, list_claims, list_pending_users
from backend.server.server_context import current_world

router = APIRouter(tags=["notifications"])


class NotificationsResponse(BaseModel):
    pending_claims: int = 0
    pending_users: int = 0
    officer_guild: str | None = None  # guild page to navigate to for claim review


@router.get("/notifications", response_model=NotificationsResponse)
async def get_notifications(request: Request) -> NotificationsResponse:
    """
    Lightweight poll endpoint — returns actionable pending counts.

    Officers  : pending_claims for characters in their guild(s).
    Admins    : pending_users (all users awaiting access) +
                pending_claims if they are also an officer somewhere.
    Others    : all zeros (bell stays hidden).
    """
    user = request.session.get("user")
    if not user:
        return NotificationsResponse()

    is_admin = user["id"] in _ADMIN_IDS
    disc_id = user["id"]

    pending_claims = 0
    pending_users = 0
    officer_guild: str | None = None

    # ── Admin: count all users awaiting access ───────────────────────────────
    if is_admin:
        pending_users = len(await list_pending_users())

    # ── Officer (or admin who is also an officer): find guilds via cache ─────
    claims_data = await get_active_claims(disc_id, world=current_world())
    approved_chars = [c["character_name"] for c in claims_data["approved"]]

    if approved_chars:
        # Resolve guild names from the in-memory character cache (no Census call)
        guilds_seen: set[str] = set()
        approved_lower = {c.lower() for c in approved_chars}
        for char_name in approved_chars:
            cache_key = char_cache_key(char_name, current_world())
            cached, _ = character_cache.get_stale(cache_key)
            if cached is not None and getattr(cached, "guild_name", None):
                guilds_seen.add(cached.guild_name)

        if guilds_seen:
            all_pending = await list_claims(status="pending", world=current_world())
            counted_ids: set[int] = set()

            # Gather roster rank maps concurrently — each is cached after first
            # fetch, so subsequent polls are cheap, and the gather only matters
            # when a user belongs to multiple guilds.
            guilds_list = list(guilds_seen)
            rank_maps = await asyncio.gather(*[_roster_rank_map(g) for g in guilds_list])
            for guild_name, rank_map in zip(guilds_list, rank_maps, strict=True):
                # Check officer status inline (avoids a redundant second call)
                if not any(rank_map.get(n) in _OFFICER_RANKS for n in approved_lower):
                    continue

                new_ids = {
                    c["id"]
                    for c in all_pending
                    if c["id"] not in counted_ids and c["character_name"].lower() in rank_map
                }
                if new_ids:
                    counted_ids.update(new_ids)
                    officer_guild = guild_name  # last guild with claims wins

            pending_claims = len(counted_ids)

    return NotificationsResponse(
        pending_claims=pending_claims,
        pending_users=pending_users,
        officer_guild=officer_guild,
    )
