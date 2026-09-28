"""
GET /api/supporters — Discord IDs + display names of users holding the
'supporter' role. Logged-in users only.

The frontend fetches this once per session, caches the IDs in memory, and
checks membership locally when rendering any username (to slap a 👑 badge
next to supporter names). The set is tiny (low double-digits at most for a
niche community site), and the alternative — joining role info into every
endpoint that returns a Discord ID — would balloon many response schemas
for one cosmetic feature.

It used to be unauthenticated (2026-09-28 privacy review): raw Discord IDs
of real people were readable by anyone on the internet without a login,
which no other endpoint allows. The whole app sits behind the Discord
login gate, so gating this one costs nothing for real users; the Support
page now gets display names from here too instead of "Supporter #1234".

Cache strategy: module-level list, populated on first request, busted on
any /api/admin/users/{discord_id}/roles/supporter grant/revoke (see the
admin route — it calls `invalidate()` here after a successful write) and
on account erasure.
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
