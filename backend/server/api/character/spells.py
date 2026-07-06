"""GET /character/{name}/spells — per-character scribed-spells tier rollup.

Carved out of the original 933-line web/routes/character.py.
"""

from __future__ import annotations

from collections import Counter

from fastapi import HTTPException, Request
from pydantic import BaseModel

from backend.census.constants import SPELL_TIER_ORDER as _TIER_ORDER
from backend.eq2db.spells import DB_PATH as _SPELLS_DB
from backend.eq2db.spells import character_upgradeable_spells as _character_upgradeable_spells
from backend.server.api.character import router
from backend.server.api.character.views import _build_char_response
from backend.server.cache import character_cache
from backend.server.core.cache_keys import char_cache_key
from backend.server.core.census_lifecycle import shared_census_client
from backend.server.limiter import limiter
from backend.server.server_context import current_world


class SpellEntryResponse(BaseModel):
    name: str
    tier: str
    level: int
    spell_type: str
    icon_id: int | None = None
    icon_backdrop: int | None = None


class CharacterSpellsResponse(BaseModel):
    character_name: str
    spells: list[SpellEntryResponse]
    tier_counts: dict[str, int]  # all _TIER_ORDER keys present
    tiers_present: list[str]  # ordered subset that have > 0 spells


@router.get("/character/{name}/spells", response_model=CharacterSpellsResponse)
@limiter.limit("30/minute")
async def get_character_spells(request: Request, name: str) -> CharacterSpellsResponse:
    """Return a character's deduplicated spell list resolved from the local spells DB.

    Spell IDs come from the character record that was already fetched (and cached)
    when the character page loaded — no extra Census call needed.
    """
    if not _SPELLS_DB.exists():
        raise HTTPException(status_code=503, detail="Spells database not available")

    # Use the cached character record (populated on first character page load).
    # Fall back to fetching if somehow the cache was cold.
    cache_key = char_cache_key(name, current_world())
    cached, _ = character_cache.get_stale(cache_key)

    if cached is not None:
        char_name = cached.name
        spell_ids = cached.spell_ids
    else:
        async with shared_census_client() as client:
            char = await client.get_character(name, current_world())
        if char is None:
            raise HTTPException(status_code=404, detail=f"Character '{name}' not found on {current_world()}")
        result = _build_char_response(char)
        character_cache.set(cache_key, result)
        char_name = result.name
        spell_ids = result.spell_ids

    if not spell_ids:
        return CharacterSpellsResponse(
            character_name=char_name, spells=[], tier_counts={t: 0 for t in _TIER_ORDER}, tiers_present=[]
        )

    # Canonical "owned upgradeable spells at best tier" list — shared with the
    # upgrade-materials checker so the two never drift (see the helper's docstring
    # for the given_by-gate history). Sort by level for display.
    rows = _character_upgradeable_spells(spell_ids)
    rows.sort(key=lambda r: r.get("level") or 0)

    count = Counter(r.get("tier_name") or "Unknown" for r in rows)

    return CharacterSpellsResponse(
        character_name=char_name,
        spells=[
            SpellEntryResponse(
                name=r.get("name") or "",
                tier=r.get("tier_name") or "Unknown",
                level=r.get("level") or 0,
                spell_type=r.get("type") or "",
                icon_id=r.get("icon_id"),
                icon_backdrop=r.get("icon_backdrop"),
            )
            for r in rows
        ],
        tier_counts={t: count.get(t, 0) for t in _TIER_ORDER},
        tiers_present=[t for t in _TIER_ORDER if count.get(t, 0) > 0],
    )
