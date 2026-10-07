"""Shared primary-character + primary-guild resolution (cache-only, no Census).

Call sites that need extra fallback logic (e.g. zones.py falls back to
the most-recent parsed guild) apply that fallback after the helper returns.
"""

from __future__ import annotations

from typing import Any

from backend.server.cache import character_cache
from backend.server.core.cache_keys import char_cache_key
from backend.server.db import get_active_claims


def get_primary_claim(claims_payload: dict) -> dict[str, Any] | None:
    """Return the ``is_primary=True`` row from a ``get_active_claims`` payload
    (a dict with an ``approved`` list)."""
    for claim in claims_payload.get("approved") or []:
        if claim.get("is_primary"):
            return claim
    return None


async def cached_primary_guild(
    discord_id: str,
    world: str,
) -> tuple[str | None, str | None]:
    """Return (primary_character_name, guild_name) for ``discord_id``.

    Cheap path: get_active_claims → primary claim → character_cache lookup
    → guild_name from cached row. Both members of the returned tuple may be
    None (no primary claim, or primary character not in cache).

    Callers that need a fallback (e.g. "most recent parsed guild") apply
    it themselves after this returns ``(_, None)`` — see backend/server/api/zones.py
    for the canonical pattern.
    """
    claims = await get_active_claims(discord_id, world=world)
    primary = get_primary_claim(claims)
    if primary is None:
        return None, None
    char_name = primary.get("character_name")
    if not char_name:
        return None, None
    cached, _ = character_cache.get_stale(char_cache_key(char_name, world))
    if cached is None:
        return char_name, None
    return char_name, getattr(cached, "guild_name", None) or None
