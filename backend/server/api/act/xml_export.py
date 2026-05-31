"""ACT XML export helpers — serialise trigger/spell-timer rows to ACT's
``spell_timers.xml`` format. Used by the export endpoints in triggers.py and
spell_timers.py.
"""

from __future__ import annotations

from xml.sax.saxutils import quoteattr

# ---------------------------------------------------------------------------
# Low-level serialisers
# ---------------------------------------------------------------------------


def _bool_attr(b: bool | int) -> str:
    """ACT serialises booleans as `True`/`False` (capitalised, .NET style)."""
    return "True" if b else "False"


def _trigger_to_xml(trigger: dict) -> str:
    """Render one `act_triggers` row as a <Trigger ... /> element. The
    attribute *order* matches ACT's own output — not strictly required for
    parsing, but it makes XML diffs against an authored file cleaner."""
    return (
        "<Trigger "
        f"Active={quoteattr(_bool_attr(trigger['active']))} "
        f"Regex={quoteattr(trigger['regex'])} "
        f"SoundData={quoteattr(trigger['sound_data'] or '')} "
        f'SoundType="{int(trigger["sound_type"])}" '
        f"CategoryRestrict={quoteattr(_bool_attr(trigger['category_restrict']))} "
        f"Category={quoteattr(trigger['category'] or '')} "
        f"Timer={quoteattr(_bool_attr(trigger['timer']))} "
        f"TimerName={quoteattr(trigger['timer_name'] or '')} "
        f"Tabbed={quoteattr(_bool_attr(trigger['tabbed']))} />"
    )


def _spell_to_xml(spell: dict) -> str:
    """Render one `act_spell_timers` row as a <Spell ... /> element."""
    return (
        "<Spell "
        f"Checked={quoteattr(_bool_attr(spell['checked']))} "
        f"Name={quoteattr(spell['name'])} "
        f'Timer="{int(spell["timer_duration_s"])}" '
        f"OnlyMasterTicks={quoteattr(_bool_attr(spell['only_master_ticks']))} "
        f"Restrict={quoteattr(_bool_attr(spell['restrict']))} "
        f"Absolute={quoteattr(_bool_attr(spell['absolute_']))} "
        f"StartWav={quoteattr(spell['start_wav'] or '')} "
        f"WarningWav={quoteattr(spell['warning_wav'] or '')} "
        f'WarningValue="{int(spell["warning_value"])}" '
        f"RadialDisplay={quoteattr(_bool_attr(spell['radial_display']))} "
        f"Modable={quoteattr(_bool_attr(spell['modable']))} "
        f"Tooltip={quoteattr(spell['tooltip'] or '')} "
        f'FillColor="{int(spell["fill_color"])}" '
        f"Panel1={quoteattr(_bool_attr(spell['panel1']))} "
        f"Panel2={quoteattr(_bool_attr(spell['panel2']))} "
        f'RemoveValue="{int(spell["remove_value"])}" '
        f"Category={quoteattr(spell['category'] or '')} "
        f"RestrictCategory={quoteattr(_bool_attr(spell['restrict_category']))} />"
    )


def build_xml(triggers: list[dict], spells: list[dict]) -> str:
    """Assemble the full ACT-compatible XML document.

    Both lists may be empty; an empty document is valid and parses cleanly
    in ACT (it just adds nothing on import)."""
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<Config>",
        "    <CustomTriggers>",
    ]
    for t in triggers:
        parts.append("        " + _trigger_to_xml(t))
    parts.append("    </CustomTriggers>")
    parts.append("    <SpellTimers>")
    for s in spells:
        parts.append("        " + _spell_to_xml(s))
    parts.append("    </SpellTimers>")
    parts.append("    <SettingsSerializer />")
    parts.append("</Config>")
    return "\n".join(parts) + "\n"


def safe_filename(s: str) -> str:
    """Make a string safe for an HTTP Content-Disposition filename. ACT
    doesn't care about the name when importing, but the browser save
    dialog defaults to it."""
    out = "".join(c if c.isalnum() or c in "-_." else "_" for c in s)
    return out.strip("_") or "trigger"


def spell_timers_referenced_by(triggers: list[dict], all_spells: list[dict]) -> list[dict]:
    """Pick out the spell timers a list of triggers actually references via
    `timer_name`. Dedup by lower-cased name (the table's UNIQUE key) so the
    export doesn't emit duplicate <Spell> rows when multiple triggers share
    one timer."""
    referenced: set[str] = {(t["timer_name"] or "").lower() for t in triggers if t.get("timer") and t.get("timer_name")}
    if not referenced:
        return []
    by_lower = {s["name"].lower(): s for s in all_spells}
    out: list[dict] = []
    for name_lower in referenced:
        s = by_lower.get(name_lower)
        if s is not None:
            out.append(s)
    return out
