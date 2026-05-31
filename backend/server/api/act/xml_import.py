"""ACT XML paste-import parser.

Accepts both ACT's verbose XML (``<Trigger Regex="..." ...>``, what
spell_timers.xml exports) AND the "shareable" short-attribute format you
get from ACT's right-click → Copy as Shareable XML
(``<Trigger R="..." SD="..." ST="3" CR="F" C="..." T="T" TN="..." Ta="F" />``).

The input can be:
  * A single ``<Trigger />`` or ``<Spell />`` element (the usual paste).
  * Multiple sibling elements at the top level.
  * A wrapping ``<root>``, ``<Triggers>``, ``<CustomTriggers>``, or full
    ``<Config>`` block.
The parser wraps the input in a synthetic root if needed so ElementTree
can parse it either way.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from fastapi import HTTPException


def _import_bool(s: str | None, default: bool = False) -> bool:
    """ACT booleans come in three forms across the long/short variants:
    ``True``/``False``, ``T``/``F``, ``true``/``false`` (occasionally
    ``1``/``0``). Accept all of them."""
    if s is None:
        return default
    v = s.strip().lower()
    if v in ("true", "t", "1", "yes"):
        return True
    if v in ("false", "f", "0", "no"):
        return False
    return default


def _import_int(s: str | None, default: int = 0) -> int:
    try:
        return int((s or "").strip())
    except (TypeError, ValueError):
        return default


def _trigger_from_element(el: ET.Element) -> dict:
    """Map a ``<Trigger>`` element (verbose or short form) to the keyword
    args of ``raids_db.upsert_act_trigger``. ``category`` is kept as-is here
    so the route layer can decide whether to restamp to the mob name."""

    def attr(*keys: str, default: str | None = None) -> str | None:
        for k in keys:
            v = el.get(k)
            if v is not None:
                return v
        return default

    return {
        "active": _import_bool(attr("Active", "A"), default=True),
        "regex": attr("Regex", "R", default="") or "",
        "sound_data": attr("SoundData", "SD", default="") or "",
        "sound_type": _import_int(attr("SoundType", "ST"), default=3),
        "category_restrict": _import_bool(attr("CategoryRestrict", "CR")),
        "category": attr("Category", "C"),
        "timer": _import_bool(attr("Timer", "T")),
        "timer_name": (attr("TimerName", "TN") or "").strip() or None,
        "tabbed": _import_bool(attr("Tabbed", "Ta")),
    }


def _spell_from_element(el: ET.Element) -> dict:
    """Map a ``<Spell>`` element to upsert_act_spell_timer kwargs.

    Accepts both ACT's verbose form (``Name``/``Timer``/``OnlyMasterTicks`` …
    as written by ``spell_timers.xml``) and the "shareable" short form ACT's
    right-click → Copy as Shareable XML produces:

      ``N`` Name, ``T`` Timer, ``OM`` OnlyMasterTicks, ``R`` Restrict,
      ``A`` Absolute, ``SW`` StartWav, ``WW`` WarningWav, ``WV`` WarningValue,
      ``RD`` RadialDisplay, ``M`` Modable, ``Tt`` Tooltip, ``FC`` FillColor,
      ``P1`` Panel1, ``P2`` Panel2, ``RV`` RemoveValue, ``C`` Category,
      ``RC`` RestrictCategory, ``Ch`` Checked.

    Single-letter keys are reused across element types (Trigger's ``R`` =
    Regex vs Spell's ``R`` = Restrict, Trigger's ``T`` = Timer bool vs
    Spell's ``T`` = duration int) — that's only safe because Trigger and
    Spell elements are parsed by separate functions."""

    def attr(*keys: str, default: str | None = None) -> str | None:
        for k in keys:
            v = el.get(k)
            if v is not None:
                return v
        return default

    return {
        "name": (attr("Name", "N") or "").strip(),
        "checked": _import_bool(attr("Checked", "Ch")),
        "timer_duration_s": _import_int(attr("Timer", "T"), default=30),
        "only_master_ticks": _import_bool(attr("OnlyMasterTicks", "OMT", "OM")),
        "restrict": _import_bool(attr("Restrict", "R")),
        "absolute_": _import_bool(attr("Absolute", "Abs", "A")),
        "start_wav": attr("StartWav", "SW", default="") or "",
        "warning_wav": attr("WarningWav", "WW", default="") or "",
        "warning_value": _import_int(attr("WarningValue", "WV"), default=10),
        "radial_display": _import_bool(attr("RadialDisplay", "RD")),
        "modable": _import_bool(attr("Modable", "M")),
        "tooltip": attr("Tooltip", "Tt", default="") or "",
        "fill_color": _import_int(attr("FillColor", "FC"), default=-16776961),
        "panel1": _import_bool(attr("Panel1", "P1"), default=True),
        "panel2": _import_bool(attr("Panel2", "P2")),
        "remove_value": _import_int(attr("RemoveValue", "RV"), default=-15),
        "category": attr("Category", "C"),
        "restrict_category": _import_bool(attr("RestrictCategory", "RC")),
    }


def parse_import_xml(text: str) -> tuple[list[dict], list[dict]]:
    """Parse a paste-friendly XML chunk from ACT.

    Returns ``(triggers, spell_timers)`` — both lists of plain dicts ready
    to feed to the ``upsert_act_*`` helpers. Raises ``HTTPException(400)``
    on unparseable input. ``Trigger`` elements with no ``Regex``/``R``
    attribute are dropped silently — same for ``Spell`` with no ``Name``."""
    body = (text or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Empty XML body")

    # Strip the XML decl if present, then always wrap in a synthetic root —
    # this is what lets the parser accept a bare `<Trigger ... />` paste,
    # multiple sibling elements, or a pre-wrapped block all the same way.
    if body.lstrip().startswith("<?"):
        end = body.find("?>")
        if end == -1:
            raise HTTPException(status_code=400, detail="Malformed XML declaration")
        body = body[end + 2 :].strip()

    wrapped = f"<__import_root>{body}</__import_root>"
    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid XML: {exc}") from exc

    triggers = [_trigger_from_element(t) for t in root.iter("Trigger") if (t.get("Regex") or t.get("R"))]
    spells = [_spell_from_element(s) for s in root.iter("Spell") if (s.get("Name") or s.get("N"))]

    if not triggers and not spells:
        raise HTTPException(
            status_code=400,
            detail="No <Trigger> or <Spell> elements found in XML",
        )

    return triggers, spells
