"""Thin shim — preserved for backwards-compatible imports.

The 1098-line module has been split into web/routes/act/ (BE-052):
  web/routes/act/_shared.py      — models + encounter resolution
  web/routes/act/triggers.py     — trigger CRUD + XML export + paste-import
  web/routes/act/spell_timers.py — spell-timer CRUD + XML export
  web/routes/act/xml_export.py   — ACT XML serialisation helpers
  web/routes/act/xml_import.py   — ACT XML paste-import parser

``web/app.py`` imports ``router`` from this module — that import continues
to work via the re-export below. All other consumers that imported
``TriggerEntry``, ``SpellTimerEntry``, or ``parse_import_xml`` etc. from
this module also continue to work.
"""

from backend.server.api.act import router  # noqa: F401
from backend.server.api.act._shared import SpellTimerEntry, TriggerEntry  # noqa: F401
from backend.server.api.act.xml_import import parse_import_xml  # noqa: F401
