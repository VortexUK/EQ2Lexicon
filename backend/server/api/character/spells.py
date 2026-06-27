"""GET /character/{name}/spells — per-character scribed-spells tier rollup.

Carved out of the original 933-line web/routes/character.py.
"""

from __future__ import annotations

from collections import Counter

from fastapi import HTTPException, Request
from pydantic import BaseModel

from backend.census.constants import SPELL_TIER_ORDER as _TIER_ORDER
from backend.eq2db.spells import DB_PATH as _SPELLS_DB
from backend.eq2db.spells import SpellRow as _SpellRow
from backend.eq2db.spells import find_by_ids as _spell_find_by_ids
from backend.eq2db.spells import load_blocklist as _load_spell_blocklist
from backend.eq2db.spells import strip_roman as _strip_roman
from backend.eq2db.spells import unique_highest_entries as _unique_highest_rows
from backend.eq2db.spells import upgradeable_crcs as _upgradeable_crcs
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

    # Bulk DB lookup — one query for all IDs
    spell_db: dict[int, _SpellRow] = _spell_find_by_ids(spell_ids)

    # Show every *upgradeable* spell the character owns, at its best tier —
    # regardless of how it was acquired. A spell is upgradeable when its line
    # has a tier ladder (Apprentice → Grandmaster); single-tier utility casts
    # (Cure, Resurrect, Soothe, …) are excluded. The old `given_by=='spellscroll'`
    # gate was wrong: it dropped spells obtained from the class trainer
    # (given_by='classtraining') or still at their auto-granted base tier
    # (given_by='class'), so a character who trained Restoration VI instead of
    # scribing a scroll saw only the lower-rank scroll they happened to own.
    # AA abilities (given_by='alternateadvancement') live in the AA tab, not here.
    blocklist = _load_spell_blocklist()
    candidate = [
        r
        for r in spell_db.values()
        if (r.get("level") or 0) > 0
        and r.get("type") in ("spells", "arts")
        and r.get("given_by") != "alternateadvancement"
        and _strip_roman(r.get("name") or "").lower() not in blocklist
    ]
    upgradeable = _upgradeable_crcs({r.get("crc") for r in candidate})
    rows = [r for r in candidate if r.get("crc") in upgradeable]

    # Deduplicate: per base name+type keep the highest-level entry (highest rank)
    rows = _unique_highest_rows(rows)
    rows.sort(key=lambda r: r.get("level") or 0)

    count = Counter(r.get("tier_name") or "Unknown" for r in rows)

    return CharacterSpellsResponse(
        character_name=char_name,
        spells=[
            SpellEntryResponse(
                name=r["name"],
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
