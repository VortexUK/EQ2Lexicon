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
  * Attack-driven proc triggers ("On any combat or spell hit this spell
    has a 50% chance to cast Bolt of Power…") open a proc block: the
    indented lines below them are the proc's own effects and its damage
    becomes ``procs`` entries (trigger kind + chance + components) — how
    AA passives like Bolt of Power are modeled. Defensive triggers
    ("When damaged…") are not modeled (they need incoming-hit rates).
  * Other indentation-0 lines produce components — remaining indented
    lines are sub-effects of unmatched triggers and any damage there
    goes to ``unparsed_damage`` (conditional, not modeled).
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
    #: Generated from an "Applies X ... every N seconds" pulse wrapper.
    #: A from_pulse component with NO duration is a maintained toggle
    #: ("Until Cancelled" in game — Exorcise).
    from_pulse: bool


class ProcDef(TypedDict):
    trigger: str  # 'any_hit' | 'melee_hit' | 'ability_cast' | 'spell_cast' | 'when_damaged' | 'target_cast'
    chance_pct: float  # 100 for "will cast" / "may cast"
    name: str  # the proc spell's name (display)
    #: Rate-limited procs ("Triggers about 3.0 times per minute.") — when
    #: set, the rate replaces the trigger-event count as the proc source.
    per_minute: float | None
    #: "Grants a total of N triggers of the spell." — a per-application
    #: trigger budget (Slothful Spirit's 3 Sloth's Habitat hits).
    trigger_count: float | None
    components: list[DamageComponent]


class ParsedEffects(TypedDict):
    components: list[DamageComponent]
    procs: list[ProcDef]  # attack-driven proc damage (Bolt of Power etc.)
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

#: Attack-driven proc trigger lines ("On any combat or spell hit this spell
#: has a 50% chance to cast Bolt of Power on target of attack.") plus the
#: DEFENSIVE damage-shield form ("When damaged this spell will cast Shock
#: of Light on target's attacker." — the Templar Mythical's Divine Light).
#: The proc's own damage sits INDENTED below the trigger. Defensive procs
#: fire on INCOMING hits; the engine rates them from a user-set
#: incoming-hits/min knob (default 0 = off).
_PROC_TRIGGER_RE = re.compile(
    r"^(?:On (?P<trigger>any combat or spell hit|a melee hit|a combat hit|a ranged hit|a hit|"
    r"a hostile ability cast|a hostile spell cast|a hostile spell hit|a hostile combat hit|"
    r"a spell cast|an ability cast)"
    r"|(?P<damaged>When damaged(?: with a melee weapon)?))"
    r" this spell (?:has an? (?P<chance>[\d.]+)% chance to cast|will cast|may cast) "
    r"(?P<name>[^.]+?)(?: on (?:target|caster)[^.]*)?(?:\.|$)"
    # Trailing sentences ("This effect normalizes based off of a three
    # second triggering...", "Lasts for N seconds.") are ignored, except
    # the rate clause captured separately below.
)

#: "Triggers about 3.0 times per minute." — rate-limited proc clause.
_PROC_RATE_RE = re.compile(r"Triggers about (?P<rate>[\d.]+) times? per minute")

#: Self-applied PULSE wrapper: "Applies Exorcise instantly and every 6
#: seconds." (optionally "...  Lasts for 12.0 seconds.") with the damage
#: INDENTED below. Covers Consecrate, Open Wounds, Netherealm, Defile,
#: Exorcise, Reversal — beneficial/self rows whose real payload is
#: hostile pulsing damage.
_APPLIES_PULSE_RE = re.compile(
    r"^Applies (?P<name>.+?)(?P<inst> instantly and)? every (?P<interval>[\d.]+) seconds?\."
    r"(?:\s+Lasts for (?P<dur>[\d.]+) seconds?\.)?\s*$"
)

_PROC_TRIGGER_KIND = {
    "any combat or spell hit": "any_hit",
    "a melee hit": "melee_hit",
    "a combat hit": "melee_hit",
    "a ranged hit": "melee_hit",
    "a hit": "melee_hit",
    "a hostile ability cast": "ability_cast",
    "a hostile spell cast": "spell_cast",
    "a hostile spell hit": "spell_cast",
    "a hostile combat hit": "melee_hit",
    # On a HOSTILE ability (a debuff on the mob), "On a spell cast" means
    # the TARGET's casts (Slothful Spirit → Sloth's Habitat).
    "a spell cast": "target_cast",
    "an ability cast": "target_cast",
}

#: "Grants a total of 3 triggers of the spell." — per-application budget.
_TRIGGER_COUNT_RE = re.compile(r"^Grants a total of (?P<count>[\d.]+) triggers? of the spell\.?$")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


#: "Increases <Stat> of <target scope> by <N>[%]." — the stat-buff line
#: shape on group/raid/ally buffs ("Increases Haste of group members (AE)
#: by 30.5."). Only stats the simulator models are mapped; everything else
#: stays visible in the effect lines but contributes no mods.
_STAT_LINE_RE = re.compile(
    r"^Increases (?P<stat>[A-Za-z' -]+?) of (?P<tgt>group members \(AE\)|raid and group members \(AE\)|target|caster)"
    r"(?: and [^b]*?)? by (?P<amt>[\d,]+(?:\.\d+)?)%?\.?$"
)

#: Stat phrase → frontend BuffMods key (additive percentage points, except
#: the flat ability mod). Multi Attack IS the double-attack stat.
_STAT_MOD_KEYS = {
    "haste": "hastePct",
    "attack speed": "hastePct",
    "dps": "dpsModPct",
    "multi attack": "doubleAttackPct",
    "double attack": "doubleAttackPct",
    "crit chance": "critChancePct",
    "crit bonus": "critBonusPct",
    "ability modifier": "abilityModFlat",
    "ability casting speed": "castSpeedPct",
    "casting speed": "castSpeedPct",
    "ability reuse speed": "reuseSpeedPct",
    "reuse speed": "reuseSpeedPct",
    "potency": "potencyPct",
    "fervor": "fervorPct",
}


#: "Increases base damage of spells and combat arts by 25%." (item) /
#: "Increases the base spell damage of the cleric by 10%." /
#: "Increases the summoner's base damage by 8%." (AA). The word "base" is
#: load-bearing: plain "Increases spell damage by 25%" (Smite Wrath) is
#: verifiably NOT applied in-game and must not match. "Improves the base
#: damage by N%" (per-ability Enhance nodes) is also excluded — the verb
#: distinguishes global multipliers from single-ability boosts.
_BASE_DAMAGE_BONUS_RE = re.compile(
    r"^Increases (?:the )?(?:[a-z']+ )?base(?: spell)? damage (?:of [^.]*? )?by (?P<amt>[\d.]+)%\.?$"
)

#: Hidden set-bonus SPELL effects (never in census sheet stats):
#: "Improves speed at which the cleric casts by 33%." and
#: "Decrease the sorcerer's spell reuse time by 5%."
_CAST_SPEED_EFFECT_RE = re.compile(r"Improves speed at which [^.]*? casts by (?P<amt>[\d.]+)%")
_REUSE_EFFECT_RE = re.compile(r"Decreases? the [^.]*? (?:spell |ability )?reuse time by (?P<amt>[\d.]+)%")


def parse_speed_bonuses(lines: list[str]) -> tuple[float, float]:
    """(cast_speed_pct, reuse_pct) from hidden set-bonus effect text."""
    cast = 0.0
    reuse = 0.0
    for line in lines:
        m = _CAST_SPEED_EFFECT_RE.search(line)
        if m:
            cast += float(m.group("amt"))
        m = _REUSE_EFFECT_RE.search(line)
        if m:
            reuse += float(m.group("amt"))
    return cast, reuse


def parse_flat_effect_procs(lines: list[str], name: str) -> list[ProcDef]:
    """Proc defs from FLAT sibling lines (set-bonus descriptiontags carry
    the trigger, the Inflicts line(s) and any 'If ...' condition as
    unindented siblings in arbitrary order)."""
    trigger: re.Match | None = None
    rate: float | None = None
    condition: str | None = None
    comps: list[DamageComponent] = []
    for raw in lines:
        line = raw.strip()
        m = _PROC_TRIGGER_RE.match(line)
        if m is not None:
            trigger = m
            r = _PROC_RATE_RE.search(line)
            rate = float(r.group("rate")) if r else None
            continue
        if line.startswith("If "):
            condition = line.rstrip(".")
            continue
        parsed = parse_damage_line(line)
        if parsed:
            comps.extend(parsed)
    if trigger is None or not comps:
        return []
    for c in comps:
        c["condition"] = condition
    return [
        {
            "trigger": _PROC_TRIGGER_KIND[trigger.group("trigger")],
            "chance_pct": float(trigger.group("chance")) if trigger.group("chance") else 100.0,
            "name": name,
            "per_minute": rate,
            "trigger_count": None,
            "components": comps,
        }
    ]


def parse_base_damage_bonus_pct(lines: list[str]) -> float:
    """Sum of 'increases base damage by N%' bonuses in the given effect
    lines (item effects + AA passives) — additive with the primary-stat
    bonus in the validated damage model."""
    total = 0.0
    for line in lines:
        m = _BASE_DAMAGE_BONUS_RE.match(line.strip())
        if m is not None:
            total += float(m.group("amt"))
    return total


def parse_stat_mods(lines: list[str]) -> dict[str, float]:
    """Modelable stat mods from a buff's effect lines → {BuffMods key:
    amount}. Duplicate stats sum. Unmapped stats (attributes, mitigation,
    skills, regen…) are ignored — the caller shows the raw lines."""
    mods: dict[str, float] = {}
    for line in lines:
        m = _STAT_LINE_RE.match(line.strip())
        if m is None:
            continue
        key = _STAT_MOD_KEYS.get(m.group("stat").strip().lower())
        if key is None:
            continue
        amt = _num(m.group("amt"))
        if amt == 0:
            continue  # era-drifted zero lines ("Increases Fervor by 0.0")
        mods[key] = mods.get(key, 0.0) + amt
    return mods


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


class AbilityAdjustment(TypedDict):
    """A per-ability timing modifier from an AA/focus effect line
    ('Reduces the reuse time of Cacophony of Blades by 30 seconds.')."""

    targets: list[str]  # lowercased ability base names it applies to
    kind: str  # 'reuse' | 'duration'
    amount: float
    is_pct: bool
    line: str  # the raw effect line (for UI provenance)


#: "Improves/Reduces [the] reuse [speed|time] [of X [and Y]] by N [%|seconds]"
_ABILITY_REUSE_ADJ_RE = re.compile(
    r"^(?:Improves|Reduces) (?:the )?reuse(?: speed| time)?"
    r"(?: of (?P<targets>[^.]+?))? by (?P<amt>[\d,]+(?:\.\d+)?)\s*(?P<unit>%|percent|seconds?)"
)
#: "Increases/Improves/Extends [the] duration [of X [and Y]] by N [%|seconds]"
_ABILITY_DURATION_ADJ_RE = re.compile(
    r"^(?:Increases|Improves|Extends) (?:the )?duration"
    r"(?: of (?P<targets>[^.]+?))? by (?P<amt>[\d,]+(?:\.\d+)?)\s*(?P<unit>%|percent|seconds?)"
)
#: Enhance:/Focus: node names carry the SUBJECT ability the node's
#: self-referential lines ("Increases duration by 3 seconds.") apply to.
_AA_SUBJECT_PREFIX_RE = re.compile(r"^(?:Enhance|Focus):\s*(?P<subject>.+)$")


def aa_subject_of(node_name: str) -> str | None:
    """'Enhance: Cacophony of Blades' → 'Cacophony of Blades'."""
    m = _AA_SUBJECT_PREFIX_RE.match(node_name.strip())
    return m.group("subject").strip() if m else None


def parse_ability_adjustments(lines: list[str], subject: str | None = None) -> list[AbilityAdjustment]:
    """Per-ability reuse/duration modifiers from AA effect text. Lines
    naming no target ('Improves reuse speed by 30 seconds') apply to
    ``subject`` — the ability the Enhance:/Focus: node is about."""
    out: list[AbilityAdjustment] = []
    for raw in lines:
        line = raw.strip()
        for rx, kind in ((_ABILITY_REUSE_ADJ_RE, "reuse"), (_ABILITY_DURATION_ADJ_RE, "duration")):
            m = rx.match(line)
            if m is None:
                continue
            raw_targets = m.group("targets")
            if raw_targets:
                targets = [t.strip().lower() for t in raw_targets.split(" and ") if t.strip()]
            elif subject:
                targets = [subject.lower()]
            else:
                continue
            out.append(
                {
                    "targets": targets,
                    "kind": kind,
                    "amount": _num(m.group("amt")),
                    "is_pct": m.group("unit") in ("%", "percent"),
                    "line": line,
                }
            )
            break
    return out


#: Curated BASE components for AA rows census stores UNSCALED ("Inflicts
#: 1 disease damage on target instantly and every 4 seconds" on lv-70
#: Rabies). AAs have no spellscroll text to rescue them, so these bases
#: were reverse-engineered from an in-game tooltip through the validated
#: chain — tooltip = B x (chain + 1/2) [+ full ability mod on the primary
#: hit] — and therefore scale correctly for any character's stats.
#:
#: Rabies (rank 1) fitted on Menwardiir across TWO gear loadouts
#: (choker: chain+1/2 = 3.3601, AM 1125 → 1,367-1,408 / 192-233 /
#: 392 / 385-467; pendant: chain+1/2 = 2.8901, AM 1198 → 1,406-1,442 /
#: 165-201 / 414 / 331-401 — every value reproduced to <=0.3%). The
#: Rabies II termination package: its DOT takes the full chain like the
#: main components; its HIT is a PROC payload (tiny base + AM/3 — the
#: gear swap moved it by exactly delta-AM/3), modeled as a
#: trigger_count=1 proc so the proc damage rule applies.
STATIC_AA_BASES: dict[str, dict] = {
    "rabies": {
        "components": [
            {"kind": "hit", "min_dmg": 72.0, "max_dmg": 84.2, "school": "disease", "target_scope": "single", "condition": None},
            {"kind": "dot", "min_dmg": 57.1, "max_dmg": 69.3, "school": "disease", "target_scope": "single",
             "condition": None, "interval_s": 4.0, "duration_s": 16.0},
            {"kind": "dot", "min_dmg": 114.6, "max_dmg": 139.0, "school": "disease", "target_scope": "single",
             "condition": None, "interval_s": 4.0, "duration_s": 16.0},
        ],
        "procs": [
            {
                "trigger": "termination",
                "chance_pct": 100.0,
                "name": "Rabies II",
                "per_minute": None,
                "trigger_count": 1.0,
                "components": [
                    {"kind": "hit", "min_dmg": 5.1, "max_dmg": 5.1, "school": "disease", "target_scope": "single", "condition": None},
                ],
            }
        ],
    },
}


def static_overrides_for(name: str) -> tuple[list[DamageComponent], list[ProcDef]] | None:
    """Curated replacement components+procs for a census-unscaled AA, or
    None. Returns fresh copies — callers mutate these downstream."""
    entry = STATIC_AA_BASES.get(name.strip().lower())
    if entry is None:
        return None
    comps = [dict(c) for c in entry["components"]]
    procs = [{**p, "components": [dict(c) for c in p["components"]]} for p in entry.get("procs", [])]
    return comps, procs  # type: ignore[return-value]


def extract_damage_pairs(lines: list[str]) -> list[tuple[float, float]]:
    """(min, max) of every Inflicts line regardless of indentation or
    role — a value-level view used by AA band interpolation when the high
    band's text shape drifts from the low band's (e.g. Exorcise's lv100
    row loses the pulse wrapper and indents its Inflicts line)."""
    out: list[tuple[float, float]] = []
    for line in lines:
        m = _INFLICTS_RE.match(line.strip())
        if m is not None:
            mn = _num(m.group("min"))
            out.append((mn, _num(m.group("max")) if m.group("max") else mn))
    return out


def parse_effect_lines(effects: list[dict]) -> ParsedEffects:
    """The full effects JSON of one spell row → structured components.

    ``effects`` is spells.db's parsed JSON: [{"description": str,
    "indentation": int}, ...]."""
    components: list[DamageComponent] = []
    procs: list[ProcDef] = []
    unparsed: list[str] = []
    lines: list[str] = []
    lasts_for: float | None = None

    # Open proc block: set when a trigger line matches; the following
    # DEEPER lines are the proc's own effects (its damage becomes proc
    # components, not unparsed). Pulse blocks work the same way for the
    # "Applies X every N seconds" wrapper.
    active_proc: ProcDef | None = None
    active_pulse: re.Match | None = None

    def _flush_proc() -> None:
        nonlocal active_proc, active_pulse
        if active_proc is not None and active_proc["components"]:
            procs.append(active_proc)
        active_proc = None
        active_pulse = None

    entries = effects or []
    for i, entry in enumerate(entries):
        text = ((entry or {}).get("description") or "").strip()
        if not text:
            continue
        lines.append(text)
        indentation = (entry or {}).get("indentation") or 0

        if indentation != 0:
            if active_proc is not None:
                comps = parse_damage_line(text)
                if comps:
                    for comp in comps:
                        comp["condition"] = None
                    active_proc["components"].extend(comps)
                else:
                    count = _TRIGGER_COUNT_RE.match(text)
                    if count is not None:
                        active_proc["trigger_count"] = float(count.group("count"))
                continue
            if active_pulse is not None:
                # The pulse wrapper carries the cadence; the indented line
                # carries values + target scope. "instantly and" ⇒ an
                # immediate hit plus the recurring dot.
                interval = float(active_pulse.group("interval"))
                dur = float(active_pulse.group("dur")) if active_pulse.group("dur") else None
                for comp in parse_damage_line(text) or []:
                    comp["from_pulse"] = True
                    if comp.get("kind") != "hit":
                        components.append(comp)  # already periodic — keep as parsed
                        continue
                    comp["condition"] = None
                    if active_pulse.group("inst"):
                        hit_copy: DamageComponent = dict(comp)  # type: ignore[assignment]
                        components.append(hit_copy)
                    comp["kind"] = "dot"
                    comp["interval_s"] = interval
                    comp["duration_s"] = dur
                    components.append(comp)
                continue
            # Indented lines are conditional sub-effects (procs, triggers).
            # Damage there is real but unmodeled — surface it, don't sum it.
            # (An "If ..." line here is a condition already attached to the
            # damage line ABOVE it — see the lookahead below.)
            if "damage" in text.lower() and text.startswith("Inflicts"):
                unparsed.append(text)
            continue

        # Back at top level — any open proc/pulse block is complete.
        _flush_proc()

        pulse = _APPLIES_PULSE_RE.match(text)
        if pulse is not None:
            active_pulse = pulse
            continue

        trig = _PROC_TRIGGER_RE.match(text)
        if trig is not None:
            rate = _PROC_RATE_RE.search(text)
            kind = "when_damaged" if trig.group("damaged") else _PROC_TRIGGER_KIND[trig.group("trigger")]
            active_proc = {
                "trigger": kind,
                "chance_pct": float(trig.group("chance")) if trig.group("chance") else 100.0,
                "name": trig.group("name").strip(),
                "per_minute": float(rate.group("rate")) if rate else None,
                "trigger_count": None,
                "components": [],
            }
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

    _flush_proc()
    return {
        "components": components,
        "procs": procs,
        "lasts_for_s": lasts_for,
        "unparsed_damage": unparsed,
        "lines": lines,
    }
