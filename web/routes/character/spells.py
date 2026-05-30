"""GET /character/{name}/spells — per-character scribed-spells tier rollup.

Carved out of the original 933-line web/routes/character.py.
"""

from __future__ import annotations

from collections import Counter

from fastapi import HTTPException, Request
from pydantic import BaseModel

from census.constants import SPELL_TIER_ORDER as _TIER_ORDER
from census.spells_db import DB_PATH as _SPELLS_DB
from census.spells_db import SpellRow as _SpellRow
from census.spells_db import find_by_ids as _spell_find_by_ids
from census.spells_db import load_blocklist as _load_spell_blocklist
from census.spells_db import strip_roman as _strip_roman
from census.spells_db import unique_highest_entries as _unique_highest_rows
from web.cache import character_cache
from web.lib.cache_keys import char_cache_key
from web.lib.census_lifecycle import shared_census_client
from web.limiter import limiter
from web.routes.character import router
from web.routes.character.views import _build_char_response
from web.server_context import current_world


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

    # Only include spells/arts the character explicitly scribed from a scroll.
    # given_by='spellscroll' is the sole upgradable category — it covers both
    # mage spells and fighter/scout combat arts (confirmed against example data).
    # given_by='class' entries are auto-granted abilities (Invisibility, Call
    # Servant, base combat art ranks etc.) that are permanently fixed in tier.
    blocklist = _load_spell_blocklist()
    rows = [
        r
        for r in spell_db.values()
        if (r.get("level") or 0) > 0
        and r.get("type") in ("spells", "arts")
        and r.get("given_by") == "spellscroll"
        and _strip_roman(r.get("name") or "").lower() not in blocklist
    ]

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
