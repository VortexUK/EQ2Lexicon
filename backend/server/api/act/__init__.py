"""ACT triggers + spell timers routes; the combined ``router`` is registered via backend/server/api/act_triggers.py."""

from __future__ import annotations

from fastapi import APIRouter

from backend.server.api.act import categories, pack, spell_timers, triggers

router = APIRouter()
router.include_router(triggers.router)
router.include_router(spell_timers.router)
router.include_router(pack.router)
router.include_router(categories.router)

# Re-export the shared models for consumers that import them directly
# (e.g. tests/server/test_act_triggers.py).
from backend.server.api.act._shared import SpellTimerEntry, TriggerEntry  # noqa: E402,F401
