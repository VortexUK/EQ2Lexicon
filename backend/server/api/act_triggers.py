"""Re-export shim for the ACT trigger routes (now in backend/server/api/act/)."""

from backend.server.api.act import router  # noqa: F401
from backend.server.api.act._shared import SpellTimerEntry, TriggerEntry  # noqa: F401
from backend.server.api.act.xml_import import parse_import_xml  # noqa: F401
