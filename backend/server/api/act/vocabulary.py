"""Closed vocabularies for the EQ2Parser-enrichment tag fields.

Mirrors EQ2Parser's ``EQ2Parser.Core.Triggers.Vocabulary`` — the parser,
the pack endpoint, and the web editors must agree on these or tags drift.
Damage schools are spelled the way the combat log spells them; control
effects use tooltip vocabulary (the log says "silenced", tooltips say
Stifle — curators speak tooltip).
"""

from __future__ import annotations

DAMAGE_SCHOOLS: tuple[str, ...] = (
    "crushing",
    "slashing",
    "piercing",
    "heat",
    "cold",
    "magic",
    "mental",
    "divine",
    "disease",
    "poison",
    "focus",
)

CONTROL_EFFECTS: tuple[str, ...] = (
    "stun",
    "stifle",
    "mez",
    "fear",
    "root",
    "snare",
    "daze",
    "charm",
    "knockup",
)


def normalize_damage_type(value: str) -> str:
    """Validate + canonicalise a comma-joined school list, preserving the
    caller's order (dominant school first — it drives the parser's flame
    colour). Unknown tokens raise ValueError → Pydantic 422."""
    seen: list[str] = []
    for token in value.split(","):
        school = token.strip().lower()
        if not school:
            continue
        if school not in DAMAGE_SCHOOLS:
            raise ValueError(f"unknown damage school {school!r} (allowed: {', '.join(DAMAGE_SCHOOLS)})")
        if school not in seen:
            seen.append(school)
    return ", ".join(seen)


def normalize_control_effect(value: str) -> str:
    effect = value.strip().lower()
    if effect and effect not in CONTROL_EFFECTS:
        raise ValueError(f"unknown control effect {effect!r} (allowed: {', '.join(CONTROL_EFFECTS)})")
    return effect
