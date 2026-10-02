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
#: PARTIAL unscaled rows (Glacial Strike: hit 61-67 genuine, dot 10-11
#: junk) evade the level heuristic. Relative rule from a scan of the
#: level 60-80 AA universe: every bad line is <= 25 next to a sibling
#: >= 4x bigger; the smallest LEGIT line observed is 84.
SUSPECT_RELATIVE_MAX = 25.0
SUSPECT_RELATIVE_RATIO = 4.0


def is_suspect_low_damage(max_dmg: float, level: int) -> bool:
    """True when a damage value is implausibly low for the spell's level —
    census unscaled-tooltip text, not a real number."""
    return max_dmg > 0 and level > 0 and max_dmg * SUSPECT_DAMAGE_LEVEL_RATIO < level


def is_suspect_relative(max_dmg: float, biggest_sibling_max: float) -> bool:
    """True when a damage value is tiny next to a sibling component in the
    SAME row — the partial-unscaled census signature."""
    return (
        0 < max_dmg <= SUSPECT_RELATIVE_MAX
        and biggest_sibling_max >= SUSPECT_RELATIVE_RATIO * max_dmg
    )


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
    #: Lifeburn's "N points of damage for each health point consumed":
    #: per application = per_hp_rate × hp_fraction × caster max health —
    #: FLAT, outside the coefficient chain (the in-game 9/HP is static
    #: across gear). min/max_dmg are 0 on such components.
    per_hp_rate: float | None
    #: Fraction of the caster's max health consumed per application
    #: (user-observed "roughly 25%" per tick — estimate pending a log).
    hp_fraction: float | None
    #: Auto-scaled class-granted ranks (Wrath): the tooltip is the BARE
    #: chain — no ability mod, no school flat, no ½-flat constant.
    no_flat_mod: bool


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

#: Mid-line "Lasts for 12.0 seconds." (item proc wrappers carry it inline).
_INLINE_LASTS_RE = re.compile(r"Lasts for (?P<secs>[\d.]+) seconds?")


class ProcTriggerInfo(TypedDict):
    trigger: str
    chance_pct: float
    name: str
    per_minute: float | None
    lasts_for_s: float | None


def parse_proc_trigger_line(line: str, *, self_source: bool = False) -> ProcTriggerInfo | None:
    """A single proc-wrapper line → its trigger metadata, or None. Used by
    the gear derivation to tell PROC-GRANTED effects (Wand of Crystallized
    Plasma's 'may cast Plasma Boost … Lasts for 12.0 seconds … 1.8 times
    per minute') apart from permanent 'When Equipped' bonuses — the lines
    indented under a trigger belong to a TEMP buff, never to the
    always-on derived modifiers.

    ``self_source=True`` reads the line in WORN-ITEM context, where "On a
    spell cast" means the WEARER's own casts; the default reads it in
    hostile-debuff context, where it means the TARGET's casts (Slothful
    Spirit → Sloth's Habitat)."""
    m = _PROC_TRIGGER_RE.match(line.strip())
    if m is None:
        return None
    rate = _PROC_RATE_RE.search(line)
    lasts = _INLINE_LASTS_RE.search(line)
    kind_map = _PROC_TRIGGER_KIND_SELF if self_source else _PROC_TRIGGER_KIND
    kind = "when_damaged" if m.group("damaged") else kind_map[m.group("trigger")]
    return {
        "trigger": kind,
        "chance_pct": float(m.group("chance")) if m.group("chance") else 100.0,
        "name": m.group("name").strip(),
        "per_minute": float(rate.group("rate")) if rate else None,
        "lasts_for_s": float(lasts.group("secs")) if lasts else None,
    }

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

#: The same phrases read from a WORN item's effect text, where the caster
#: IS the source: "On a spell cast" is the wearer's own cast.
_PROC_TRIGGER_KIND_SELF = {
    **_PROC_TRIGGER_KIND,
    "a spell cast": "spell_cast",
    "an ability cast": "ability_cast",
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
    r"^Increases (?P<stat>[A-Za-z', -]+?) of (?P<tgt>group members \(AE\)|raid and group members \(AE\)|target|caster)"
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
    "ability recovery speed": "recoverySpeedPct",
    "recovery speed": "recoverySpeedPct",
    "potency": "potencyPct",
    "fervor": "fervorPct",
    # Weapon Damage Bonus (Berserker's raid-wide Destructive Rage:
    # "Increases Weapon Damage of raid and group members (AE) by 3.3.")
    # — a PERCENT, like base damage but for auto-attack swings only.
    "weapon damage": "weaponDamagePct",
    # Ally/group doublecast grants (Conjuror's Unabate on target).
    "ability doublecast": "doublecastPct",
    # Attributes — FLAT adds. Only pertinent when they hit the character's
    # PRIMARY attribute: the frontend maps "<primary_attr>Flat" onto
    # primary_stat and ignores the rest (AGI on a templar does nothing;
    # STA deliberately absent).
    "str": "strFlat",
    "strength": "strFlat",
    "agi": "agiFlat",
    "agility": "agiFlat",
    "wis": "wisFlat",
    "wisdom": "wisFlat",
    "int": "intFlat",
    "intelligence": "intFlat",
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
#: Worn-item spell-timing effects — the "Reduces <cast|reuse> time of
#: <scope> spells by N percent" family ("Arcane Recovery I" on Sash of
#: Secrets, Nagol's Treasure's cast-time cut). These are spell-effect
#: lines, NOT the Casting/Reuse Speed character stats, so census sheet
#: stats never include them — genuinely hidden. Full scope survey of
#: items.db: all / hostile / beneficial / Subjugation-based / healing;
#: only all+hostile speed the damage rotation (beneficial/healing don't,
#: and skill-scoped cuts would need per-ability skill tracking), and a
#: scopeless "Reduces casting time by 10%" form also exists.
#: "Increases Reuse Speed of caster by N%" styles are deliberately NOT
#: parsed — that IS the sheet stat (game-computed), already counted.
_ITEM_SPELL_TIME_RE = re.compile(
    r"^Reduces (?P<what>cast(?:ing)?|reuse) time (?:of (?P<scope>all|hostile|beneficial) spells )?"
    r"by (?P<amt>[\d.]+) ?(?:percent|%)\.?$",
    re.IGNORECASE,
)


def parse_item_spell_timing(lines: list[str]) -> list[tuple[str, str, float]]:
    """Worn-item spell-timing cuts → (kind, scope, pct) triples, kind in
    'cast'|'reuse', scope in 'all'|'hostile'|'beneficial' (a scopeless
    line reads as 'all'). Skill-scoped variants ('Subjugation-based') and
    'healing' deliberately don't match — they'd need per-ability skill
    tracking. Scope is preserved so a hostile-only cut never speeds a
    beneficial cast and vice versa."""
    out: list[tuple[str, str, float]] = []
    for raw in lines:
        m = _ITEM_SPELL_TIME_RE.match(raw.strip())
        if m is None:
            continue
        kind = "cast" if m.group("what").lower().startswith("cast") else "reuse"
        scope = (m.group("scope") or "all").lower()
        out.append((kind, scope, float(m.group("amt"))))
    return out


def parse_speed_bonuses(lines: list[str]) -> tuple[float, float]:
    """(cast_speed_pct, reuse_pct) from hidden set-bonus effect text —
    these class-wide set effects apply to everything the class casts.
    Worn-item scoped timing cuts go through parse_item_spell_timing."""
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


#: "Increases disease damage done by spells by up to 30." (Spooky Bone
#: Hoop) — a SCHOOL-SPECIFIC flat damage add on worn gear. It behaves
#: exactly like ability mod on matching-school abilities only: full on
#: the primary hit, never on ticks. Pinned by Menludeth's Lifeburn
#: tooltip (hit − tick = 657 = sheet AM 627 + 30 exactly at both ends)
#: and the same +30 closes Soulrot VII's hit residual. A school the
#: character has no spells in contributes nothing.
_SCHOOL_DAMAGE_FLAT_RE = re.compile(
    r"^Increases (?P<schools>[a-z]+(?: and [a-z]+)*) damage done by (?:spells|combat arts)"
    r" by (?:up to )?(?P<amt>[\d,]+(?:\.\d+)?)\.?$",
    re.IGNORECASE,
)


def parse_school_damage_flat(lines: list[str]) -> dict[str, float]:
    """{lowercased school: summed flat amount} from 'Increases <school>
    damage done by spells by up to N.' gear lines."""
    out: dict[str, float] = {}
    for raw in lines:
        m = _SCHOOL_DAMAGE_FLAT_RE.match(raw.strip())
        if m is None:
            continue
        for school in m.group("schools").lower().split(" and "):
            school = school.strip()
            if school:
                out[school] = out.get(school, 0.0) + _num(m.group("amt"))
    return out


#: Ally-buff speed grammar (Time Compression: "Improves recovery speed of
#: spells by 40%."). The "of spells" anchor keeps per-ability AA lines
#: ("Improves casting and recovery speed by 60%" — Enhance Jab) excluded.
_SPEED_OF_SPELLS_RE = re.compile(
    r"^Improves (?P<cast>casting and )?recovery speed of spells by (?P<amt>[\d,]+(?:\.\d+)?)%\.?$"
)


#: Shadowknight raid-wide Unholy Strength: "Increase spell damage of
#: group and raid members by 5%." — USER-VERIFIED functional in game
#: (unlike the self-targeted Smite Wrath phrasing, which is provably
#: dead text — the raid-scope anchor keeps that one excluded).
_RAID_SPELL_DAMAGE_RE = re.compile(
    r"^Increases? spell damage of (?:group and raid|raid and group) members by (?P<amt>[\d.]+)%\.?$"
)


def parse_stat_mods(lines: list[str]) -> dict[str, float]:
    """Modelable stat mods from a buff's effect lines → {BuffMods key:
    amount}. Duplicate stats sum. Unmapped stats (attributes, mitigation,
    skills, regen…) are ignored — the caller shows the raw lines."""
    mods: dict[str, float] = {}
    seen_raid_dmg: set[str] = set()
    for line in lines:
        rd = _RAID_SPELL_DAMAGE_RE.match(line.strip())
        if rd is not None:
            # Census duplicates this exact line inside Unholy Strength's
            # effect list — identical lines count once.
            amt = _num(rd.group("amt"))
            if amt and line.strip() not in seen_raid_dmg:
                seen_raid_dmg.add(line.strip())
                mods["baseDamagePct"] = mods.get("baseDamagePct", 0.0) + amt
            continue
        sp = _SPEED_OF_SPELLS_RE.match(line.strip())
        if sp is not None:
            amt = _num(sp.group("amt"))
            if amt:
                mods["recoverySpeedPct"] = mods.get("recoverySpeedPct", 0.0) + amt
                if sp.group("cast"):
                    mods["castSpeedPct"] = mods.get("castSpeedPct", 0.0) + amt
            continue
        m = _STAT_LINE_RE.match(line.strip())
        if m is None:
            continue
        amt = _num(m.group("amt"))
        if amt == 0:
            continue  # era-drifted zero lines ("Increases Fervor by 0.0")
        # Compound phrases ("AGI, STR and STA of target") grant the amount
        # to EACH listed stat — split and map every part.
        phrase = m.group("stat").strip().lower()
        for part in (p.strip() for chunk in phrase.split(",") for p in chunk.split(" and ")):
            key = _STAT_MOD_KEYS.get(part)
            if key is not None:
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
    kind: str  # 'reuse' | 'duration' | 'damage' | 'dot_damage' | 'crit_bonus' | 'cast' | 'recovery'
    amount: float
    is_pct: bool
    line: str  # the raw effect line (for UI provenance)


#: "Improves/Reduces [the] reuse [speed|time] [of X [and Y]] by N [%|seconds]"
_ABILITY_REUSE_ADJ_RE = re.compile(
    r"^(?:Improves|Reduces) (?:the )?reuse(?: speed| time)?"
    r"(?: of (?P<targets>[^.]+?))? by (?P<amt>[\d,]+(?:\.\d+)?)\s*(?P<unit>%|percent|seconds?)"
)
#: "Increases/Improves/Extends/Reduces [the] duration [of X [and Y]] by
#: N [%|seconds]" — Reduces yields a NEGATIVE amount (Enhance: Soulrot
#: compresses its dot: same ticks squeezed into the shorter duration).
_ABILITY_DURATION_ADJ_RE = re.compile(
    r"^(?P<verb>Increases|Improves|Extends|Reduces) (?:the )?duration"
    r"(?: of (?P<targets>[^.]+?))? by (?P<amt>[\d,]+(?:\.\d+)?)\s*(?P<unit>%|percent|seconds?)"
)

#: Per-ability Enhance damage line ("Increases damage by 5%.") — the
#: in-game tooltip phrases it as a potency modifier, and it fits the data
#: as a multiplier on the BASE-CHAIN part only, NOT the flat ability-mod
#: part: once the Spooky Bone Hoop's +30 disease flat was found, Soulrot
#: VII's hit back-solves to chain×1.05 + (sheet AM 627 + 30) exactly —
#: the earlier whole-tooltip fit was the coincidence 627×1.05 ≈ 627+30.
_ABILITY_DAMAGE_ADJ_RE = re.compile(r"^(?:Increases|Improves) (?:the )?damage by (?P<amt>[\d,]+(?:\.\d+)?)%\.?$")
#: "Increases overtime damage by 25%." (Enhance: Stealth Assault) — the
#: ability's DOT components only; the initial hit is untouched.
_ABILITY_OT_DAMAGE_ADJ_RE = re.compile(r"^Increases overtime damage by (?P<amt>[\d,]+(?:\.\d+)?)%\.?$")
#: "Improves the Crit Bonus by 5%." / "Improves Crit Bonus by 5." —
#: per-ability crit bonus (dealt damage only; tooltips exclude crit).
_ABILITY_CRIT_BONUS_ADJ_RE = re.compile(r"^Improves (?:the )?Crit Bonus by (?P<amt>[\d,]+(?:\.\d+)?)%?\.?$", re.IGNORECASE)
#: "Improves [the] casting speed by 0.5 seconds." (48 Enhance nodes) /
#: "Improves casting speed by 50%." — per-ability cast-time cuts.
#: "Improves casting and recovery speed by 75%" (Enhance Jab) trims both.
_ABILITY_CAST_ADJ_RE = re.compile(
    r"^(?:Improves|Increases|Reduces) (?:the )?casting(?P<rec> and recovery)? speed"
    r"(?: of (?P<targets>[^.]+?))? by (?P<amt>[\d,]+(?:\.\d+)?)\s*(?P<unit>%|percent|seconds?)"
)
#: Enhance:/Focus: node names carry the SUBJECT ability the node's
#: self-referential lines ("Increases duration by 3 seconds.") apply to.
_AA_SUBJECT_PREFIX_RE = re.compile(r"^(?:Enhance|Focus):\s*(?P<subject>.+)$")


#: Era-curated AA base-damage overrides (lowercased node name → %).
#: Smite Wrath's census text reads "Increases spell damage by 25%." — a
#: value that never fit any tooltip — but Menludiir's Divine Smite VII /
#: Divine Strike VII pair back-solves +5 additive in the base-damage
#: bucket with it specced (rank 1 is the only rank). The plain
#: "spell damage %" phrasing stays unparsed; this table carries the
#: measured value instead.
STATIC_AA_BASE_DAMAGE: dict[str, float] = {
    "smite wrath": 5.0,
}


#: Curated bases for CLASS-GRANTED ranks the game AUTO-SCALES to the
#: character's level (census carries only the original-level values —
#: Wrath's row reads 22-27 while the level-80 tooltip reads 2,522-3,031).
#: Scroll-bought grey ranks do NOT auto-scale (Divine Smite II-IV read
#: far lower in game). Bases are reverse-engineered through the chain:
#: Wrath's observed ratio (1.202) matches a bare-chain tooltip with NO
#: ability mod and NO ½-flat — components carry no_flat_mod. Fitted on
#: Menludiir at level 80 (chain 4.109); a buff-tick re-read moving by
#: exactly delta-chain x B would confirm the no-AM structure.
STATIC_CLASS_SPELL_BASES: dict[str, list[DamageComponent]] = {
    "wrath": [
        {"kind": "hit", "min_dmg": 613.8, "max_dmg": 737.6, "school": "divine", "target_scope": "single",
         "condition": None, "no_flat_mod": True},
    ],
}


def static_class_spell_base_for(name: str) -> list[DamageComponent] | None:
    """Curated auto-scaled components for a class-granted rank, or None."""
    base = STATIC_CLASS_SPELL_BASES.get(name.strip().lower())
    return [dict(c) for c in base] if base is not None else None  # type: ignore[misc]


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
        matched_self = False
        for rx, kind in (
            (_ABILITY_DAMAGE_ADJ_RE, "damage"),
            (_ABILITY_OT_DAMAGE_ADJ_RE, "dot_damage"),
            (_ABILITY_CRIT_BONUS_ADJ_RE, "crit_bonus"),
        ):
            dm = rx.match(line)
            if dm is not None and subject:
                out.append(
                    {
                        "targets": [subject.lower()],
                        "kind": kind,
                        "amount": _num(dm.group("amt")),
                        "is_pct": True,
                        "line": line,
                    }
                )
                matched_self = True
                break
        if matched_self:
            continue
        cm = _ABILITY_CAST_ADJ_RE.match(line)
        if cm is not None:
            raw_targets = (cm.group("targets") or "").strip().lower()
            # Stat/scope phrasings belong to other parsers: "Improves
            # casting and recovery speed of spells" is the ally-buff
            # global; "Increases Casting Speed of caster" is a sheet stat.
            if raw_targets in ("spells", "caster", "the caster", "target", "pet") or "members" in raw_targets:
                continue
            if raw_targets:
                targets = [t.strip().lower() for t in raw_targets.split(" and ") if t.strip()]
            elif subject:
                targets = [subject.lower()]
            else:
                continue
            amount = _num(cm.group("amt"))
            is_pct = cm.group("unit") in ("%", "percent")
            out.append({"targets": targets, "kind": "cast", "amount": amount, "is_pct": is_pct, "line": line})
            if cm.group("rec"):
                out.append({"targets": targets, "kind": "recovery", "amount": amount, "is_pct": is_pct, "line": line})
            continue
        for rx, kind in ((_ABILITY_REUSE_ADJ_RE, "reuse"), (_ABILITY_DURATION_ADJ_RE, "duration")):
            m = rx.match(line)
            if m is None:
                continue
            raw_targets = m.groupdict().get("targets")
            if raw_targets:
                targets = [t.strip().lower() for t in raw_targets.split(" and ") if t.strip()]
            elif subject:
                targets = [subject.lower()]
            else:
                continue
            amount = _num(m.group("amt"))
            if kind == "duration" and m.groupdict().get("verb") == "Reduces":
                amount = -amount
            out.append(
                {
                    "targets": targets,
                    "kind": kind,
                    "amount": amount,
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
    # Glacial Strike (mystic AA): the HIT line is genuine census data
    # (61-67 melee at rank 5 reproduces the in-game 1,305-1,322 with the
    # live ability mod), but the DOT line is unscaled at EVERY band
    # (10-11 / 2-3 / 2-3 / 11-12) — and sneaks past the suspect-low
    # heuristic. Dot base reversed from the in-game tooltip 2,487-2,749
    # every 4s (Menwardiir rank 5, chain+1/2 = 3.3601; ticks carry no
    # ability mod so the fit is drift-independent). Duration 16s from the
    # tooltip.
    "glacial strike": {
        "components": [
            {"kind": "hit", "min_dmg": 61.0, "max_dmg": 67.0, "school": "melee", "target_scope": "single", "condition": None},
            {"kind": "dot", "min_dmg": 740.2, "max_dmg": 818.1, "school": "cold", "target_scope": "single",
             "condition": None, "interval_s": 4.0, "duration_s": 16.0},
        ],
        "procs": [],
    },
    # Lifeburn (necro EoF AA): census bands are era-drifted junk (the
    # level-70 flat pair 109-121 doesn't reproduce in game, and the
    # per-HP coefficient reads 18 at every band where RoK shows 9). The
    # flat base reverses EXACTLY from Menludeth's tooltip through the
    # validated chain (chain+1/2 = 2.7327, INT 844 / pot 47.6): dot
    # 80-88 → B 29.3-32.2, and hit 737-745 − flat mod 657 (sheet AM 627
    # + Spooky Bone Hoop 30 disease) → the SAME base at both ends. The
    # per-HP part ("9 points per health point consumed, instantly and
    # every second") is FLAT — static 9/HP, burning ~25% of the caster's
    # max health per application (user-observed; log pending). The
    # in-game cap "based off the target's maximum health" is not
    # modeled (raid bosses don't hit it). Duration 10s, 1s ticks.
    "lifeburn": {
        "components": [
            {"kind": "hit", "min_dmg": 29.3, "max_dmg": 32.2, "school": "disease", "target_scope": "single", "condition": None},
            {"kind": "dot", "min_dmg": 29.3, "max_dmg": 32.2, "school": "disease", "target_scope": "single",
             "condition": None, "interval_s": 1.0, "duration_s": 10.0},
            {"kind": "hit", "min_dmg": 0.0, "max_dmg": 0.0, "school": "disease", "target_scope": "single",
             "condition": None, "per_hp_rate": 9.0, "hp_fraction": 0.25},
            {"kind": "dot", "min_dmg": 0.0, "max_dmg": 0.0, "school": "disease", "target_scope": "single",
             "condition": None, "interval_s": 1.0, "duration_s": 10.0, "per_hp_rate": 9.0, "hp_fraction": 0.25},
        ],
        "procs": [],
    },
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
