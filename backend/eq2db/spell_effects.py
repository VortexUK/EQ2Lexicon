"""Effect-text parser for the rotation simulator — pure, no DB access.

spells.db stores damage ONLY as English effect lines ("Inflicts 447 - 746
melee damage on target", "... every 4 seconds"); no numeric columns exist.
This module turns those lines into structured damage components the
simulator can do arithmetic on. One canonical grammar covers every
observed level-60–80 hostile line (verified against the live catalogue);
anything that doesn't match is carried in ``unparsed_damage`` and badged
in the UI — NEVER silently dropped.

Parsing rules (each backed by observed rows):
  * ``Inflicts <min>[ - <max>] <school> damage on <target>`` → one 'hit'
    (flat lines get min == max).
  * ``... instantly and every N seconds`` → BOTH a 'hit' and a 'dot' with
    the same values (matches in-game behaviour, e.g. Frostbite).
  * ``... every N seconds`` (no "instantly") → pure 'dot'; bare
    ``every second`` means interval 1.0.
  * ``on target`` / ``on target encounter`` / ``on targets in Area of
    Effect`` → ``target_scope`` 'single' / 'encounter' / 'aoe' (the sim
    multiplies encounter/aoe components by its target count).
  * A damage line immediately followed by a DEEPER line starting with
    ``If `` is conditional damage — census places the condition AFTER its
    effect, indented one level ("Inflicts 754 - 922 divine damage on
    target." / "  If target is undead"). The condition text is attached
    to the component (``condition``); the sim gates it on target toggles.
    No condition-first layout exists in the catalogue (verified: 0 rows).
  * Only indentation-0 lines produce components — indented lines are
    sub-effects of procs/triggers ("Applies X on termination…") and any
    damage there goes to ``unparsed_damage`` (conditional, not modeled).
  * Heals, threat, debuffs, requirement text → ignored (kept in ``lines``).

Unit constants live here so every consumer shares one source of truth:
  * ``SPELL_DURATION_DIVISOR`` — items.db ``spell_duration`` is HUNDREDTHS
    of a second (verified: Dark Pyre VI 1000 → 10 s; Tempest 800 → 8 s).
  * ``RECOVERY_DIVISOR`` — spells.db ``recovery_secs`` is 10× inflated:
    the census ``recovery_secs_tenths`` field actually carries hundredths
    and ``spell_to_row`` divides by 10, so the stored 5.0 is really the
    universal in-game 0.5 s. Normalised at read time; fixing ingestion +
    re-downloading the 117 MB catalogue is a separate follow-up.
"""

from __future__ import annotations

import re
from typing import TypedDict

#: items.db spell_duration → seconds (field is hundredths of a second).
SPELL_DURATION_DIVISOR = 100.0
#: spells.db recovery_secs → real seconds (stored value is 10× inflated).
RECOVERY_DIVISOR = 10.0
#: A beneficial spell with a duration at or under this is "temp-buff shaped"
#: and belongs in the rotation; longer buffs are permanent and already
#: baked into the character sheet stats.
TEMP_BUFF_MAX_DURATION_S = 300.0
#: DoT lines usually carry no duration in the effect text; when the
#: items.db join has none either, the engine assumes this (flagged "est.").
FALLBACK_DOT_DURATION_S = 12.0
#: Census renders SOME spells' damage text without scaling context, so the
#: line reads "Inflicts 1 - 2 divine damage" at level 71 (Smite Corruption,
#: Daydream, Dragonfire, Grave Sacrament, Rabies — 8 lines total in the
#: lv50-80 Master universe; the WIS/debuff text on the same rows scales
#: fine). max_dmg * RATIO < level flags these with zero false positives.
SUSPECT_DAMAGE_LEVEL_RATIO = 10.0


def is_suspect_low_damage(max_dmg: float, level: int) -> bool:
    """True when a damage value is implausibly low for the spell's level —
    census unscaled-tooltip text, not a real number."""
    return max_dmg > 0 and level > 0 and max_dmg * SUSPECT_DAMAGE_LEVEL_RATIO < level


class DamageComponent(TypedDict, total=False):
    kind: str  # 'hit' | 'dot'
    min_dmg: float
    max_dmg: float  # == min_dmg for flat lines
    school: str  # melee|ranged|crushing|slashing|piercing|heat|cold|magic|mental|divine|disease|poison|focus
    target_scope: str  # 'single' | 'encounter' | 'aoe'
    condition: str | None  # e.g. "If target is undead"; None = unconditional
    interval_s: float | None  # dot only
    duration_s: float | None  # dot only; None → caller supplies/estimates
    duration_estimated: bool


class ParsedEffects(TypedDict):
    components: list[DamageComponent]
    lasts_for_s: float | None  # top-level "Lasts for N seconds." if present
    unparsed_damage: list[str]  # damage-looking lines NOT modeled
    lines: list[str]  # every raw description, order preserved


_INFLICTS_RE = re.compile(
    r"^Inflicts (?P<min>[\d,]+(?:\.\d+)?)(?:\s*-\s*(?P<max>[\d,]+(?:\.\d+)?))?"
    r"\s+(?P<school>\w+) damage on "
    r"(?P<tgt>targets in Area of Effect|target encounter|target)"
    r"(?:\s+(?P<instant>instantly and))?"
    r"(?:\s*(?P<every>every (?:(?P<interval>[\d.]+) seconds|second)))?"
    r"\s*\.?$"
)

_LASTS_RE = re.compile(r"^Lasts for (?P<secs>[\d.,]+) seconds?\.?$")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_damage_line(text: str) -> list[DamageComponent] | None:
    """One effect line → its damage components, or None when the line is
    not a recognised top-level damage line. A single line can yield TWO
    components ("instantly and every N seconds" = hit + dot)."""
    m = _INFLICTS_RE.match(text.strip())
    if m is None:
        return None
    min_dmg = _num(m.group("min"))
    max_dmg = _num(m.group("max")) if m.group("max") else min_dmg
    school = m.group("school").lower()
    tgt = m.group("tgt")
    scope = "single" if tgt == "target" else ("encounter" if tgt == "target encounter" else "aoe")
    interval_raw = m.group("interval")
    has_every = m.group("every") is not None
    interval = float(interval_raw) if interval_raw else (1.0 if has_every else None)
    instant = m.group("instant") is not None

    base: DamageComponent = {"min_dmg": min_dmg, "max_dmg": max_dmg, "school": school, "target_scope": scope}
    if not has_every:
        return [{**base, "kind": "hit"}]
    dot: DamageComponent = {**base, "kind": "dot", "interval_s": interval, "duration_s": None}
    if instant:
        return [{**base, "kind": "hit"}, dot]
    return [dot]


def parse_effect_lines(effects: list[dict]) -> ParsedEffects:
    """The full effects JSON of one spell row → structured components.

    ``effects`` is spells.db's parsed JSON: [{"description": str,
    "indentation": int}, ...]."""
    components: list[DamageComponent] = []
    unparsed: list[str] = []
    lines: list[str] = []
    lasts_for: float | None = None

    entries = effects or []
    for i, entry in enumerate(entries):
        text = ((entry or {}).get("description") or "").strip()
        if not text:
            continue
        lines.append(text)
        indentation = (entry or {}).get("indentation") or 0

        if indentation != 0:
            # Indented lines are conditional sub-effects (procs, triggers).
            # Damage there is real but unmodeled — surface it, don't sum it.
            # (An "If ..." line here is a condition already attached to the
            # damage line ABOVE it — see the lookahead below.)
            if "damage" in text.lower() and text.startswith("Inflicts"):
                unparsed.append(text)
            continue

        parsed = parse_damage_line(text)
        if parsed is not None:
            # Census condition-after layout: a deeper "If ..." line directly
            # below makes this line's damage conditional.
            condition: str | None = None
            if i + 1 < len(entries):
                nxt = entries[i + 1] or {}
                nxt_text = (nxt.get("description") or "").strip()
                if ((nxt.get("indentation") or 0) > indentation) and nxt_text.startswith("If "):
                    condition = nxt_text.rstrip(".")
            for comp in parsed:
                comp["condition"] = condition
            components.extend(parsed)
            continue
        lasts = _LASTS_RE.match(text)
        if lasts is not None:
            lasts_for = _num(lasts.group("secs"))
            continue
        if text.startswith("Inflicts") and "damage" in text.lower():
            # A damage line the grammar doesn't cover — never drop silently.
            unparsed.append(text)

    return {
        "components": components,
        "lasts_for_s": lasts_for,
        "unparsed_damage": unparsed,
        "lines": lines,
    }
