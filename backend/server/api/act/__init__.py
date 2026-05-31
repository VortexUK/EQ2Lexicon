"""web/routes/act — ACT triggers + spell timers package.

Split from the original 1098-line web/routes/act_triggers.py (BE-052).

Sub-modules:
  _shared.py      — models (TriggerEntry, SpellTimerEntry) + encounter
                    resolution helpers shared by both endpoint modules
  triggers.py     — trigger CRUD + single/bulk XML export + paste-import
  spell_timers.py — spell-timer CRUD + single XML export
  xml_export.py   — ACT XML serialisation helpers (build_xml, safe_filename, ...)
  xml_import.py   — ACT XML paste-import parser (parse_import_xml, ...)

The combined ``router`` re-exported here is registered in web/app.py
(unchanged import: ``from web.routes.act_triggers import router``
now resolves via the thin shim at web/routes/act_triggers.py).
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.server.api.act import spell_timers, triggers

router = APIRouter()
router.include_router(triggers.router)
router.include_router(spell_timers.router)

# Re-export the shared models for consumers that import them directly
# (e.g. tests/web/test_act_triggers.py).
from backend.server.api.act._shared import SpellTimerEntry, TriggerEntry  # noqa: E402,F401
