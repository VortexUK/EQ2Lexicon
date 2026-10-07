"""GET /api/supporters — Discord IDs + display names of 'supporter' role holders.

Session-gated (raw Discord IDs must not be public). The frontend fetches the
set once and badges names locally. The module-level cache must be cleared via
``invalidate()`` on supporter grant/revoke and on account erasure.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.server import db as users_db
from backend.server.auth_deps import require_user_session as _require_user

router = APIRouter(tags=["supporters"])


class SupporterEntry(BaseModel):
    discord_id: str
    display_name: str | None = None


class SupporterListResponse(BaseModel):
    supporter_ids: list[str]
    supporters: list[SupporterEntry]


# Module-level cache. None = not yet loaded (next request fetches);
# [] = loaded and empty; any other list = loaded with content.
# Race-safe because Python list assignment is atomic and the worst-case
# race (two concurrent loaders) is a tiny duplicate DB query.
_cache: list[SupporterEntry] | None = None


def invalidate() -> None:
    """Force the next /api/supporters request to re-query the DB. Called
    from the admin grant/revoke handlers so a fresh badge appears (or
    disappears) without waiting for a server restart or cache TTL."""
    global _cache
    _cache = None


@router.get("/supporters", response_model=SupporterListResponse)
async def list_supporters(request: Request) -> SupporterListResponse:
    """Return every user holding the 'supporter' role, with their Discord
    display name where known. Session required."""
    _require_user(request)
    global _cache
    if _cache is None:
        # list_role_assignments returns {discord_id: [roles…]} for
        # every user with at least one role — one query, no N+1.
        assignments = await users_db.list_role_assignments()
        ids = sorted(discord_id for discord_id, roles in assignments.items() if "supporter" in roles)
        names = await users_db.get_display_names_for_discord_ids(ids) if ids else {}
        _cache = [SupporterEntry(discord_id=i, display_name=names.get(i)) for i in ids]
    return SupporterListResponse(supporter_ids=[s.discord_id for s in _cache], supporters=list(_cache))
