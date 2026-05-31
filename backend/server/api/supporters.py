"""
GET /api/supporters — public list of Discord IDs holding the 'supporter' role.

The frontend fetches this once per session, caches the IDs in memory, and
checks membership locally when rendering any username (to slap a 👑 badge
next to supporter names). The set is tiny (low double-digits at most for a
niche community site), the data is non-sensitive (Discord IDs are already
public in any guild the user is in), and the alternative — joining role
info into every endpoint that returns a Discord ID — would balloon many
response schemas for one cosmetic feature.

Cache strategy: module-level list, populated on first request, busted on
any /api/admin/users/{discord_id}/roles/supporter grant/revoke (see the
admin route — it calls `invalidate()` here after a successful write).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.server import db as users_db

router = APIRouter(tags=["supporters"])


class SupporterListResponse(BaseModel):
    supporter_ids: list[str]


# Module-level cache. None = not yet loaded (next request fetches);
# [] = loaded and empty; any other list = loaded with content.
# Race-safe because Python list assignment is atomic and the worst-case
# race (two concurrent loaders) is a tiny duplicate DB query.
_cache: list[str] | None = None


def invalidate() -> None:
    """Force the next /api/supporters request to re-query the DB. Called
    from the admin grant/revoke handlers so a fresh badge appears (or
    disappears) without waiting for a server restart or cache TTL."""
    global _cache
    _cache = None


@router.get("/supporters", response_model=SupporterListResponse)
async def list_supporters() -> SupporterListResponse:
    """Return all Discord IDs that hold the 'supporter' role.

    Public, unauthenticated — supporter status is a public badge by
    design and the list only contains opaque numeric Discord IDs.
    Re-fetched from the DB the first time after every invalidate().
    """
    global _cache
    if _cache is None:
        # list_role_assignments returns {discord_id: [roles…]} for
        # every user with at least one role — one query, no N+1.
        assignments = await users_db.list_role_assignments()
        _cache = sorted(discord_id for discord_id, roles in assignments.items() if "supporter" in roles)
    return SupporterListResponse(supporter_ids=_cache)
