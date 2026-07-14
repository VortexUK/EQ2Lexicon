"""Route package — split from the original 933-line web/routes/character.py.

Sub-modules:
  - views   — GET /character/{name}, _build_char_response, equipment helpers
  - gear_sets — GET /character/{name}/gear-sets (saved in-game equipment sets)
  - spells  — GET /character/{name}/spells
  - upgrades — GET /character/{name}/upgrade-materials + /upgrade-recipes
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["character"])

from backend.server.api.character import gear_sets as _gear_sets  # noqa: E402,F401
from backend.server.api.character import spells as _spells  # noqa: E402,F401
from backend.server.api.character import upgrades as _upgrades  # noqa: E402,F401
from backend.server.api.character import views as _views  # noqa: E402,F401

# Re-export the public response models.
# Re-export equipment response types used by tests and external consumers.
# Re-export private helpers imported by external consumers (guild.py, guild_officer.py,
# parses/ingest.py, census_refresh.py) and by the test suite.
# prewarm_character_cache is called from web/app.py startup.
from backend.server.api.character.views import (  # noqa: E402
    AdornSlotResponse,
    CharacterResponse,
    CharacterStats,
    EquipmentSlotResponse,
    _adorn_ilvl_bonus,
    _build_char_response,
    _equipment_lookup_ids,
    _heal_equipment_placeholders,
    _ilvl_from_gear,
    prewarm_character_cache,
)

__all__ = [
    "router",
    "CharacterResponse",
    "CharacterStats",
    "AdornSlotResponse",
    "EquipmentSlotResponse",
    "prewarm_character_cache",
    "_build_char_response",
    "_equipment_lookup_ids",
    "_ilvl_from_gear",
    "_adorn_ilvl_bonus",
    "_heal_equipment_placeholders",
]
