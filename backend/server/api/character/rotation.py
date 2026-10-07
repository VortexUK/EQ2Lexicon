"""GET /character/{name}/rotation-data — the rotation simulator's payload.

Everything the frontend engine needs per owned ability: cast/recast/
recovery timing (the spells catalogue), parsed damage components (effect-text parser
in backend.eq2db.spell_effects), spell duration + power cost + scaled
effect text (the items catalogue spellscroll join — preferred over the spells catalogue
effect text, which is unscaled for some spells).

Recovery normalisation: the spells catalogue ``recovery_secs`` is 10× inflated
(census units bug — see spell_effects.RECOVERY_DIVISOR); divided here so
the wire value is real seconds (0.5 for virtually every combat ability).
"""

from __future__ import annotations

import logging
import re
from typing import cast

from fastapi import HTTPException, Request
from pydantic import BaseModel

from backend.census.constants import FIGHTERS, MAGES, PRIESTS, SCOUTS
from backend.eq2db.aas import catalogue as _aas
from backend.eq2db.items import catalogue as _items
from backend.eq2db.spell_effects import (
    RECOVERY_DIVISOR,
    SPELL_DURATION_DIVISOR,
    STATIC_AA_BASE_DAMAGE,
    TEMP_BUFF_MAX_DURATION_S,
    TEMP_BUFF_MIN_DURATION_S,
    AbilityAdjustment,
    ParsedEffects,
    ProcTriggerInfo,
    aa_subject_of,
    apply_interval_overrides,
    apply_mod_key_overrides,
    apply_text_overrides,
    extract_damage_pairs,
    is_suspect_low_damage,
    is_suspect_relative,
    parse_ability_adjustments,
    parse_base_damage_bonus_pct,
    parse_effect_lines,
    parse_flat_effect_procs,
    parse_item_spell_timing,
    parse_proc_trigger_line,
    parse_school_damage_flat,
    parse_speed_bonuses,
    parse_stat_mods,
    static_class_spell_base_for,
    static_overrides_for,
)
from backend.eq2db.spells import SpellRow
from backend.eq2db.spells import catalogue as _spells
from backend.server.api.character import router
from backend.server.api.character.views import resolve_character_store_first
from backend.server.core.executor import run_sync
from backend.server.limiter import limiter
from backend.server.server_context import current_server

_log = logging.getLogger(__name__)

#: the aas catalogue uses this sentinel for "node has no spell".
_NO_SPELL_CRC = 4294967295


class DamageComponentResponse(BaseModel):
    kind: str  # 'hit' | 'dot'
    min_dmg: float
    max_dmg: float
    school: str
    target_scope: str = "single"  # 'single' | 'encounter' | 'aoe'
    condition: str | None = None  # e.g. "If target is undead"
    interval_s: float | None = None
    duration_s: float | None = None
    duration_estimated: bool = False
    # Census unscaled-tooltip disease: the damage text reads "1 - 2" on a
    # lv71 spell. The number is carried as-is but flagged so the UI can
    # badge it; calibration is the real fix.
    suspect_low_value: bool = False
    # Generated from an "Applies X ... every N seconds" pulse wrapper.
    from_pulse: bool = False
    # Lifeburn's per-HP mechanic: per application = per_hp_rate ×
    # hp_fraction × caster max health — FLAT, outside the coefficient
    # chain (min/max_dmg are 0 on such components).
    per_hp_rate: float | None = None
    hp_fraction: float | None = None
    # Auto-scaled class-granted ranks (Wrath): bare chain only — no
    # ability mod, no school flat, no ½-flat constant.
    no_flat_mod: bool = False


class ProcResponse(BaseModel):
    trigger: str  # 'any_hit' | 'melee_hit' | 'ability_cast' | 'spell_cast' | 'when_damaged' | 'target_cast'
    chance_pct: float
    name: str
    # Rate-limited procs ("Triggers about 3.0 times per minute") — when
    # set, the rate replaces the trigger-event count.
    per_minute: float | None = None
    # "Grants a total of N triggers" — a per-application budget (Slothful
    # Spirit fires its payload exactly N times per cast of the debuff).
    trigger_count: float | None = None
    components: list[DamageComponentResponse] = []


class RotationAbilityResponse(BaseModel):
    name: str
    base_name: str
    crc: int | None = None
    tier_name: str
    level: int
    spell_type: str  # 'spells' | 'arts'
    beneficial: bool
    cast_secs: float
    recast_secs: float
    recovery_secs: float  # normalised to real seconds
    target_type: str | None = None
    icon_id: int | None = None
    icon_backdrop: int | None = None
    duration_s: float | None = None  # items catalogue spell duration, seconds
    power_cost: int | None = None  # carried for later; v1 engine ignores it
    components: list[DamageComponentResponse] = []
    procs: list[ProcResponse] = []
    effect_lines: list[str] = []
    has_unparsed_damage: bool = False
    source: str = "spell"  # 'spell' | 'aa'
    rank: int | None = None  # AA rank spent (aa-sourced entries only)
    # Self-target pulse with no duration = a toggle kept up permanently
    # ("Until Cancelled" — Exorcise). Belongs in the maintained section,
    # modeled as a continuous pulse stream, not cast in rotation.
    maintained: bool = False
    # Stat mods parsed from the effect text (BuffMods keys) — set on the
    # character's OWN castable temp buffs so casting one in rotation opens
    # a window with its real effects (no curation needed).
    mods: dict[str, float] = {}
    # Per-ability multiplier from the character's own Enhance AAs
    # ("Increases damage by 5%.") — multiplies the BASE-CHAIN part only,
    # not the flat ability-mod/school-flat part (Soulrot VII + Lifeburn
    # cross-validated once the Spooky Bone Hoop's +30 disease was found).
    dmg_mod_pct: float = 0.0
    # "Increases overtime damage by N%." — DOT components only.
    dot_dmg_mod_pct: float = 0.0
    # Pre-AA base timings, set only when an AA cut changed them: cast and
    # reuse can NEVER drop below HALF the ORIGINAL base (user-verified: a
    # 5s cast with a −1s AA and +100% cast speed still floors at 2.5s),
    # so the frontend floor must reference these, not the adjusted values.
    orig_cast_secs: float | None = None
    orig_recast_secs: float | None = None
    # "Improves the Crit Bonus by N%." — per-ability crit bonus, dealt
    # damage only (tooltips exclude crit).
    crit_bonus_pct: float = 0.0
    # The character's own AA effect lines applied to this ability
    # (duration compression, reuse, damage %) — provenance for the UI.
    aa_adjustments: list[str] = []


class DerivedSourceResponse(BaseModel):
    kind: str  # 'item' | 'set' | 'aa'
    name: str
    detail: str  # the effect line / bonus description


class ItemProcBuffResponse(BaseModel):
    """A TEMP stat buff granted by a worn item's proc (Wand of
    Crystallized Plasma → Plasma Boost): rate + duration drive
    deterministic buff windows in the engine; the mods apply only while
    a window is up — never folded into the always-on derived modifiers."""

    name: str  # the granted buff's name ("Plasma Boost")
    item: str
    duration_s: float
    per_minute: float
    chance_pct: float = 100.0
    trigger: str = "spell_cast"
    mods: dict[str, float] = {}  # BuffMods keys (incl. baseDamagePct)


class DerivedModifiersResponse(BaseModel):
    """Hidden modifiers auto-derived from worn gear + adorns + active set
    bonuses + class-tree AAs — none of these appear in census sheet
    stats. Every value is user-correctable in the UI; `sources` explains
    where each came from."""

    base_damage_bonus_pct: float = 0.0
    # ALL-scope timing bonuses (set bonuses, "of all spells" item cuts) —
    # they speed every cast, so they fold into the global speed stats.
    cast_speed_bonus_pct: float = 0.0
    reuse_bonus_pct: float = 0.0
    # SCOPED worn-item timing cuts ("Reduces reuse time of hostile spells
    # by 1 percent" — Arcane Recovery): a hostile-only cut never speeds a
    # beneficial cast and vice versa, so the engine applies these per
    # ability by its beneficial flag.
    hostile_cast_pct: float = 0.0
    hostile_reuse_pct: float = 0.0
    beneficial_cast_pct: float = 0.0
    beneficial_reuse_pct: float = 0.0
    # School-specific FLAT damage from gear ("Increases disease damage
    # done by spells by up to 30." — Spooky Bone Hoop), keyed by
    # lowercased school. Behaves like ability mod but ONLY on abilities
    # whose component school matches — useless to a character with no
    # spells of that school.
    school_damage_flat: dict[str, float] = {}
    sources: list[DerivedSourceResponse] = []
    # Proc-granted temp stat buffs from worn gear (windows, not always-on).
    proc_buffs: list[ItemProcBuffResponse] = []


class CharacterRotationDataResponse(BaseModel):
    character_name: str
    cls: str | None = None
    level: int | None = None
    abilities: list[RotationAbilityResponse]
    # Always-on damage sources: AA proc passives (Bolt of Power) plus
    # active set-bonus procs (Valorous "Fiery Condemnation" etc.) — not
    # castable, modeled as attack-driven proc streams.
    passives: list[RotationAbilityResponse] = []
    derived: DerivedModifiersResponse = DerivedModifiersResponse()


def _build_component_models(
    parsed: ParsedEffects, duration_s: float | None, level: int
) -> tuple[list[DamageComponentResponse], list[ProcResponse]]:
    """Parsed effects → response models: DoT duration inheritance,
    suspect-low flagging, and proc blocks."""
    components: list[DamageComponentResponse] = []
    biggest = max((c.get("max_dmg") or 0.0 for c in parsed["components"]), default=0.0)
    for c in parsed["components"]:
        comp = dict(c)
        if comp.get("kind") == "dot" and comp.get("duration_s") is None:
            dot_dur = duration_s or parsed["lasts_for_s"]
            comp["duration_s"] = dot_dur
            comp["duration_estimated"] = dot_dur is None
        max_dmg = c.get("max_dmg") or 0.0
        # Per-HP components (Lifeburn) legitimately carry 0 base damage —
        # their value comes from the caster's health pool, not census.
        if not c.get("per_hp_rate"):
            comp["suspect_low_value"] = is_suspect_low_damage(max_dmg, level) or is_suspect_relative(max_dmg, biggest)
        components.append(DamageComponentResponse.model_validate(comp))
    procs = [
        ProcResponse(
            trigger=p["trigger"],
            chance_pct=p["chance_pct"],
            name=p["name"],
            per_minute=p["per_minute"],
            trigger_count=p.get("trigger_count"),
            components=[
                DamageComponentResponse.model_validate(
                    {**pc, "suspect_low_value": is_suspect_low_damage(pc.get("max_dmg") or 0.0, level)}
                )
                for pc in p["components"]
            ],
        )
        for p in parsed["procs"]
    ]
    return components, procs


def _is_maintained(beneficial: bool, target_type: str | None, components: list[DamageComponentResponse]) -> bool:
    """Self-target pulse with no duration = 'Until Cancelled' toggle
    (Exorcise): kept up permanently, modeled as a continuous stream."""
    return (
        beneficial
        and (target_type or "") == "self"
        and any(c.from_pulse and c.kind == "dot" and c.duration_s is None for c in components)
    )


def _interpolate_parsed(lo: ParsedEffects, hi: ParsedEffects, frac: float) -> None:
    """Blend hi-band component values into lo IN PLACE at `frac` — AA rows
    live at level bands (70/100/110…) and the game linearly interpolates
    the character's value between them (validated: Bolt of Power tooltip
    matches interp80 to 0.4%). Shapes must align or lo is left as-is."""

    def blend(a: list, b: list) -> bool:
        if len(a) != len(b) or any(x.get("kind") != y.get("kind") for x, y in zip(a, b)):
            return False
        for x, y in zip(a, b):
            x["min_dmg"] = x["min_dmg"] + (y["min_dmg"] - x["min_dmg"]) * frac
            x["max_dmg"] = x["max_dmg"] + (y["max_dmg"] - x["max_dmg"]) * frac
        return True

    if not blend(lo["components"], hi["components"]) and lo["components"]:
        # Band-text drift: high-band rows sometimes lose the pulse wrapper
        # ("Applies Exorcise." with the Inflicts line indented) and parse
        # to no components. When the hi TEXT carries exactly one damage
        # pair and every lo component shares one value pair (the pulse
        # signature), blend them all against it.
        hi_pairs = set(extract_damage_pairs(hi["lines"]))
        lo_pairs = {(c.get("min_dmg", 0.0), c.get("max_dmg", 0.0)) for c in lo["components"]}
        if len(hi_pairs) == 1 and len(lo_pairs) == 1:
            hmin, hmax = next(iter(hi_pairs))
            lmin, lmax = next(iter(lo_pairs))
            for c in lo["components"]:
                c["min_dmg"] = lmin + (hmin - lmin) * frac
                c["max_dmg"] = lmax + (hmax - lmax) * frac
    if len(lo["procs"]) == len(hi["procs"]):
        for p_lo, p_hi in zip(lo["procs"], hi["procs"]):
            blend(p_lo["components"], p_hi["components"])


def _build_aa_entries_sync(
    aa_trees: list[tuple[int, dict[str, int]]], known_crcs: set[int], char_level: int
) -> tuple[list[RotationAbilityResponse], list[RotationAbilityResponse], list[DerivedSourceResponse]]:
    """SYNC: spent AA nodes → (castable AA abilities, proc passives,
    class-wide base-damage sources).

    Node → the aas catalogue spellcrc → the spells catalogue row at the SPENT rank (tier ==
    rank; find_by_crc prefers the lowest real-level variant — the era
    row on a TLE server). Proc-carrying beneficials/innates (Bolt of
    Power) become passives; hostile castables join the rotation palette.
    CLASS-tree "increases base damage" lines (Pact of the Faithful,
    Virulence, Brainstorm…) are collected as global multiplier sources —
    subclass/shadows per-ability boosts are NOT (they modify single
    lines; the calibration factor absorbs them for now).
    """
    import json  # noqa: PLC0415

    castables: list[RotationAbilityResponse] = []
    passives: list[RotationAbilityResponse] = []
    aa_sources: list[DerivedSourceResponse] = []
    seen: set[int] = set()
    for tree_id, spent in aa_trees:
        tree = _aas.get_tree(tree_id)
        if not tree:
            continue
        is_class_tree = (tree.get("tree_type") or "") == "class"
        nodes = {n["node_id"]: n for n in tree["nodes"]}
        for node_id_str, rank in spent.items():
            if rank <= 0:
                continue
            try:
                node = nodes.get(int(node_id_str))
            except (TypeError, ValueError):
                continue
            crc = (node or {}).get("spellcrc") or 0
            if node is None or not crc or crc == _NO_SPELL_CRC or crc in known_crcs or crc in seen:
                continue
            seen.add(crc)
            # AA rows live at level bands (70/100/110…): take the band at
            # or below the character and interpolate toward the next one.
            bands = _spells.find_by_crc_bands(crc, rank)
            row = None
            row_hi = None
            frac = 0.0
            if bands:
                lows = [b for b in bands if (b.get("level") or 0) <= char_level]
                row = lows[-1] if lows else bands[0]
                above = [b for b in bands if (b.get("level") or 0) > (row.get("level") or 0)]
                if above and char_level > (row.get("level") or 0):
                    row_hi = above[0]
                    frac = (char_level - (row.get("level") or 0)) / (
                        (row_hi.get("level") or 0) - (row.get("level") or 0)
                    )
                    frac = max(0.0, min(1.0, frac))
            else:
                row = _spells.find_by_crc(crc, rank)
            if row is None:
                continue

            def _parse_row(r_: SpellRow) -> ParsedEffects:
                try:
                    return parse_effect_lines(json.loads(r_.get("effects") or "[]"))
                except (TypeError, ValueError):
                    return parse_effect_lines([])

            parsed = _parse_row(row)
            if row_hi is not None and frac > 0:
                _interpolate_parsed(parsed, _parse_row(row_hi), frac)
            # Census-unscaled AAs (Rabies reads "1 - 1"): curated bases
            # replace the junk — reverse-engineered through the validated
            # chain, so they scale with the character's stats.
            static = static_overrides_for(row.get("name") or node.get("name") or "")
            if static is not None:
                parsed["components"], parsed["procs"] = static
                parsed["unparsed_damage"] = []
            # Measured pulse-cadence corrections (Exorcise 7.1s vs census 6s).
            apply_interval_overrides(
                _spells.strip_roman(row.get("name") or node.get("name") or ""), parsed["components"]
            )
            level = row.get("level") or 0
            lasts = parsed["lasts_for_s"]
            components, procs = _build_component_models(parsed, lasts, level)
            beneficial = bool(row.get("beneficial"))
            rtype = row.get("type") or ""

            if is_class_tree:
                for line in parsed["lines"]:
                    if parse_base_damage_bonus_pct([line]) > 0 or parse_school_damage_flat([line]):
                        aa_sources.append(
                            DerivedSourceResponse(kind="aa", name=row.get("name") or node["name"], detail=line)
                        )

            # Era-curated AA base-damage overrides (ANY tree): Smite
            # Wrath's census text ("+25% spell damage") never fit a
            # tooltip; the measured value is +5 additive base damage
            # (Menludiir's Divine Smite/Strike VII pair). The detail line
            # is written in the parseable base-damage phrasing so the
            # derived-total loop picks it up like any other source.
            override_pct = STATIC_AA_BASE_DAMAGE.get((row.get("name") or node.get("name") or "").strip().lower())
            if override_pct:
                aa_sources.append(
                    DerivedSourceResponse(
                        kind="aa",
                        name=row.get("name") or node["name"],
                        detail=f"Increases base damage by {override_pct:g}%.",
                    )
                )

            entry = RotationAbilityResponse(
                name=row.get("name") or node["name"],
                base_name=_spells.strip_roman(row.get("name") or node["name"]),
                crc=crc,
                tier_name=row.get("tier_name") or "",
                level=level,
                spell_type=rtype,
                beneficial=beneficial,
                cast_secs=float(row.get("cast_secs") or 0.0),
                recast_secs=float(row.get("recast_secs") or 0.0),
                recovery_secs=float(row.get("recovery_secs") or 0.0) / RECOVERY_DIVISOR,
                target_type=row.get("target_type"),
                icon_id=row.get("icon_id") or node.get("icon_id"),
                icon_backdrop=row.get("icon_backdrop"),
                duration_s=lasts,
                components=components,
                procs=procs,
                effect_lines=parsed["lines"],
                has_unparsed_damage=bool(parsed["unparsed_damage"]),
                source="aa",
                rank=rank,
                maintained=_is_maintained(beneficial, row.get("target_type"), components),
            )

            if components and rtype in ("spells", "arts"):
                # Damage components ⇒ castable damage ability, even when
                # flagged beneficial (self-applied pulse AoEs: Exorcise).
                castables.append(entry)
            elif procs and (rtype == "pcinnates" or beneficial):
                # Always-on proc source (or a proc buff — treated always-on).
                passives.append(entry)
            elif not beneficial and rtype in ("spells", "arts") and parsed["unparsed_damage"]:
                castables.append(entry)
            elif beneficial and lasts and TEMP_BUFF_MIN_DURATION_S <= lasts <= TEMP_BUFF_MAX_DURATION_S:
                # Castable AA temp buff — carry its parsed stat mods so
                # casting it in rotation opens a window with real effects.
                entry.mods = parse_stat_mods(parsed["lines"])
                castables.append(entry)
    return castables, passives, aa_sources


def _derive_from_gear_sync(
    equip_counts: dict[int, int], char_level: int
) -> tuple[DerivedModifiersResponse, list[RotationAbilityResponse]]:
    """SYNC: worn items + adorns → hidden modifiers.

    * item/adorn effect lines: "increases base damage by N%" (Bloodthirsty
      Choker) → base-damage sources.
    * ACTIVE set bonuses (piece count ≥ requireditems, adorn sets like
      Aegis of Nem Anhk included): hidden cast/reuse-speed spell effects,
      base-damage lines, and damage procs (Valorous "Fiery Condemnation").
      Structured stat keys (basemodifier/critchance/…) are deliberately
      IGNORED — census sheet stats already include them.
    """
    import json  # noqa: PLC0415

    derived = DerivedModifiersResponse()
    passives: list[RotationAbilityResponse] = []
    ids = list(equip_counts)

    # Item/adorn effect lines, ORDER + INDENTATION aware: lines nested
    # under a proc trigger are a TEMP buff the proc grants (Wand of
    # Crystallized Plasma: "may cast Plasma Boost … Lasts for 12.0
    # seconds … 1.8 times per minute" wrapping "+8% base damage") — they
    # become rate-proc buff windows, NEVER always-on modifiers. Only
    # un-nested lines count as permanent (Bloodthirsty Choker).
    seen_lines: set[tuple[int, str]] = set()
    proc_ctx: dict[int, tuple[ProcTriggerInfo, str, int, list[str]]] = {}

    # In-game stacking rule for NAMED worn effects ('Disease Cloud VI',
    # 'Arcane Recovery I' — the adornment_list name): the same name
    # applies ONCE no matter how many copies or items carry it, while
    # different tiers (VI vs VII) are different names and stack. Items
    # whose names were all seen already are skipped wholesale (procs
    # included), and a named effect never scales by equip count.
    named = _items.named_effects_for_ids(ids)
    seen_names: set[str] = set()
    skip_items: set[int] = set()
    for item_id in ids:
        enames = named.get(item_id) or []
        if enames and all(n in seen_names for n in enames):
            skip_items.add(item_id)
        seen_names.update(enames)

    def _flush_item_proc(item_id: int) -> None:
        ctx = proc_ctx.pop(item_id, None)
        if ctx is None:
            return
        trig, item_name, _depth, lines = ctx
        mods = parse_stat_mods(lines)
        base = parse_base_damage_bonus_pct(lines)
        if base > 0:
            mods["baseDamagePct"] = mods.get("baseDamagePct", 0.0) + base
        if not mods or not trig["lasts_for_s"] or not trig["per_minute"]:
            return  # damage payloads / unrated procs aren't modeled here
        derived.proc_buffs.append(
            ItemProcBuffResponse(
                name=trig["name"],
                item=item_name,
                duration_s=trig["lasts_for_s"],
                per_minute=trig["per_minute"],
                chance_pct=trig["chance_pct"],
                trigger=trig["trigger"],
                mods=mods,
            )
        )

    last_item: int | None = None
    for item_id, name, line, indent in _items.effect_lines_for_ids(ids):
        if item_id != last_item and last_item is not None:
            _flush_item_proc(last_item)
        last_item = item_id
        if item_id in skip_items:
            continue
        ctx = proc_ctx.get(item_id)
        if ctx is not None:
            if indent > ctx[2]:
                ctx[3].append(line)
                continue
            _flush_item_proc(item_id)
        trig = parse_proc_trigger_line(line, self_source=True)
        if trig is not None:
            proc_ctx[item_id] = (trig, name, indent, [])
            continue
        if (item_id, line) in seen_lines:
            continue
        seen_lines.add((item_id, line))
        weight = 1 if named.get(item_id) else equip_counts.get(item_id, 1)
        amt = parse_base_damage_bonus_pct([line])
        if amt > 0:
            derived.base_damage_bonus_pct += amt * weight
            derived.sources.append(DerivedSourceResponse(kind="item", name=name, detail=line))
        school_flat = parse_school_damage_flat([line])
        if school_flat:
            for school, flat in school_flat.items():
                derived.school_damage_flat[school] = derived.school_damage_flat.get(school, 0.0) + flat * weight
            derived.sources.append(DerivedSourceResponse(kind="item", name=name, detail=line))
        for kind, scope, amt_pct in parse_item_spell_timing([line]):
            field = {
                ("cast", "all"): "cast_speed_bonus_pct",
                ("reuse", "all"): "reuse_bonus_pct",
                ("cast", "hostile"): "hostile_cast_pct",
                ("reuse", "hostile"): "hostile_reuse_pct",
                ("cast", "beneficial"): "beneficial_cast_pct",
                ("reuse", "beneficial"): "beneficial_reuse_pct",
            }[(kind, scope)]
            setattr(derived, field, getattr(derived, field) + amt_pct * weight)
            derived.sources.append(DerivedSourceResponse(kind="item", name=name, detail=line))
    if last_item is not None:
        _flush_item_proc(last_item)

    # Active set bonuses.
    piece_count: dict[str, int] = {}
    bonus_source: dict[str, str] = {}
    for item_id, set_name, raw_json in _items.set_bonus_rows_for_ids(ids):
        piece_count[set_name] = piece_count.get(set_name, 0) + equip_counts.get(item_id, 1)
        if raw_json:
            bonus_source.setdefault(set_name, raw_json)
    for set_name, count in piece_count.items():
        raw = bonus_source.get(set_name)
        if not raw:
            continue
        try:
            bonus_list = json.loads(raw).get("setbonus_list") or []
        except (ValueError, AttributeError):
            continue
        for bonus in bonus_list:
            if not isinstance(bonus, dict):
                continue
            req = int(bonus.get("requireditems", 0) or 0)
            if req > count:
                continue
            texts = [str(v) for k, v in bonus.items() if (str(k).startswith("descriptiontag") or k == "effect") and v]
            if not texts:
                continue
            label = f"{set_name} ({req}pc)"
            cast, reuse = parse_speed_bonuses(texts)
            if cast > 0:
                derived.cast_speed_bonus_pct += cast
                derived.sources.append(
                    DerivedSourceResponse(kind="set", name=label, detail=f"+{cast:g}% casting speed")
                )
            if reuse > 0:
                derived.reuse_bonus_pct += reuse
                derived.sources.append(DerivedSourceResponse(kind="set", name=label, detail=f"+{reuse:g}% reuse"))
            base = parse_base_damage_bonus_pct(texts)
            if base > 0:
                derived.base_damage_bonus_pct += base
                derived.sources.append(DerivedSourceResponse(kind="set", name=label, detail=f"+{base:g}% base damage"))
            for school, flat in parse_school_damage_flat(texts).items():
                derived.school_damage_flat[school] = derived.school_damage_flat.get(school, 0.0) + flat
                derived.sources.append(
                    DerivedSourceResponse(kind="set", name=label, detail=f"+{flat:g} {school} damage (spells)")
                )
            for proc in parse_flat_effect_procs(texts, label):
                comps = [
                    DamageComponentResponse.model_validate(
                        {**pc, "suspect_low_value": is_suspect_low_damage(pc.get("max_dmg") or 0.0, char_level)}
                    )
                    for pc in proc["components"]
                ]
                passives.append(
                    RotationAbilityResponse(
                        name=label,
                        base_name=label,
                        tier_name="",
                        level=char_level,
                        spell_type="setbonus",
                        beneficial=True,
                        cast_secs=0.0,
                        recast_secs=0.0,
                        recovery_secs=0.0,
                        procs=[
                            ProcResponse(
                                trigger=proc["trigger"],
                                chance_pct=proc["chance_pct"],
                                name=proc["name"],
                                per_minute=proc["per_minute"],
                                components=comps,
                            )
                        ],
                        effect_lines=texts,
                        source="set",
                    )
                )
    return derived, passives


def _build_abilities_sync(
    spell_ids: list[int],
    aa_trees: list[tuple[int, dict[str, int]]],
    equip_counts: dict[int, int],
    char_level: int,
) -> tuple[list[RotationAbilityResponse], list[RotationAbilityResponse], DerivedModifiersResponse]:
    """SYNC (executor): resolve the rotation universe (owned spells + spent
    AA abilities), parse effects, join the items catalogue's spell meta, and derive
    the hidden gear/set/AA modifiers — all catalogue reads in one hop."""
    import json  # noqa: PLC0415

    rows = _spells.character_rotation_spells(spell_ids)
    rows.sort(key=lambda r: r.get("level") or 0)

    meta = _items.spell_meta_by_names([f"{r.get('name')} ({r.get('tier_name')})" for r in rows])

    out: list[RotationAbilityResponse] = []
    spell_passives: list[RotationAbilityResponse] = []
    for r in rows:
        m = meta.get(f"{r.get('name')} ({r.get('tier_name')})") or {}

        # Effect text: prefer the spellscroll's (the items catalogue) — it carries the
        # properly SCALED numbers the live tooltip shows; the spells catalogue
        # spell-record text is unscaled for some spells (Smite Corruption
        # reads "1 - 2" where the scroll says "132 - 161"). Scroll lines are
        # uniformly base-indented (typically 1) — shift to base 0 so the
        # parser's top-level/conditional semantics hold.
        item_effects = m.get("effects") or []
        if item_effects:
            depths = [
                (e or {}).get("indentation") or 0 for e in item_effects if ((e or {}).get("description") or "").strip()
            ]
            shift = min(depths) if depths else 0
            effects = [{**e, "indentation": ((e or {}).get("indentation") or 0) - shift} for e in item_effects]
        else:
            try:
                effects = json.loads(r.get("effects") or "[]")
            except (TypeError, ValueError):
                effects = []
        parsed = parse_effect_lines(effects)

        # Class-granted ranks the game AUTO-SCALES to the character's
        # level (Wrath): census only has the original-level values, so a
        # curated chain-fitted base replaces them (no AM, no ½-flat).
        class_static = static_class_spell_base_for(r.get("name") or "")
        if class_static is not None:
            parsed["components"] = class_static
            parsed["unparsed_damage"] = []
        # Measured pulse-cadence corrections (name-keyed; Exorcise 7.1s).
        apply_interval_overrides(_spells.strip_roman(r.get("name") or ""), parsed["components"])

        raw_duration = m.get("spell_duration")
        duration_s = (raw_duration / SPELL_DURATION_DIVISOR) if raw_duration else None
        beneficial = bool(r.get("beneficial"))

        components, procs = _build_component_models(parsed, duration_s, r.get("level") or 0)

        # Permanent buffs are already baked into the character sheet stats;
        # only temp-buff-shaped beneficials belong in a rotation — UNLESS
        # the "buff" carries damage components (self-applied pulse AoEs
        # like Consecrate/Exorcise are beneficial=1/self in the data but
        # are damage abilities). Proc-carrying permanents (the Mythical's
        # Divine Light → Shock of Light damage shield) become PASSIVES —
        # their stats part is in the sheet but the proc stream is not.
        # "Rotated temp" needs recast > duration, same rule as the raid
        # buff-tiers path: a recast at or under the duration is 100%
        # maintainable — functionally until-cancelled (Inquisitor
        # Fanaticism reads 36s but recasts in 3s, Act of War 36s/2s; both
        # are concentration toggles, not rotation casts).
        rotated_temp = bool(
            duration_s
            and TEMP_BUFF_MIN_DURATION_S <= duration_s <= TEMP_BUFF_MAX_DURATION_S
            and float(r.get("recast_secs") or 0.0) > duration_s
        )
        if beneficial and not components and not rotated_temp:
            if procs:
                spell_passives.append(
                    RotationAbilityResponse(
                        name=r.get("name") or "",
                        base_name=_spells.strip_roman(r.get("name") or ""),
                        crc=r.get("crc"),
                        tier_name=r.get("tier_name") or "Unknown",
                        level=r.get("level") or 0,
                        spell_type=r.get("type") or "",
                        beneficial=True,
                        cast_secs=float(r.get("cast_secs") or 0.0),
                        recast_secs=float(r.get("recast_secs") or 0.0),
                        recovery_secs=float(r.get("recovery_secs") or 0.0) / RECOVERY_DIVISOR,
                        target_type=r.get("target_type"),
                        icon_id=r.get("icon_id"),
                        icon_backdrop=r.get("icon_backdrop"),
                        duration_s=duration_s,
                        procs=procs,
                        effect_lines=parsed["lines"],
                        has_unparsed_damage=bool(parsed["unparsed_damage"]),
                        source="spell",
                    )
                )
            continue

        out.append(
            RotationAbilityResponse(
                name=r.get("name") or "",
                base_name=_spells.strip_roman(r.get("name") or ""),
                crc=r.get("crc"),
                tier_name=r.get("tier_name") or "Unknown",
                level=r.get("level") or 0,
                spell_type=r.get("type") or "",
                beneficial=beneficial,
                cast_secs=float(r.get("cast_secs") or 0.0),
                recast_secs=float(r.get("recast_secs") or 0.0),
                recovery_secs=float(r.get("recovery_secs") or 0.0) / RECOVERY_DIVISOR,
                target_type=r.get("target_type"),
                icon_id=r.get("icon_id"),
                icon_backdrop=r.get("icon_backdrop"),
                duration_s=duration_s,
                power_cost=m.get("spell_power_cost"),
                components=components,
                procs=procs,
                effect_lines=parsed["lines"],
                has_unparsed_damage=bool(parsed["unparsed_damage"]),
                maintained=_is_maintained(beneficial, r.get("target_type"), components),
                # Own temp buffs: casting one opens a window with these.
                mods=parse_stat_mods(parsed["lines"]) if beneficial else {},
            )
        )

    known_crcs = {a.crc for a in out if a.crc}
    aa_castables, passives, aa_sources = _build_aa_entries_sync(aa_trees, known_crcs, char_level)
    out.extend(aa_castables)
    passives.extend(spell_passives)

    # The character's OWN per-ability Enhance/Focus AA adjustments
    # (duration compression, reuse, damage %) applied to their abilities.
    # A duration cut COMPRESSES the dot — same tick count squeezed into
    # the shorter window (Enhance: Soulrot −2.5s turns a 4s/1s dot into
    # 1.5s at ~0.375s ticks; in-game tooltip confirms).
    own_adjustments = _member_aa_adjustments_sync(aa_trees)
    if own_adjustments:
        for ability in out:
            base = ability.base_name.lower()
            for adj in own_adjustments:
                if base not in adj["targets"]:
                    continue
                if adj["kind"] == "damage":
                    ability.dmg_mod_pct += adj["amount"]
                elif adj["kind"] == "dot_damage":
                    ability.dot_dmg_mod_pct += adj["amount"]
                elif adj["kind"] == "crit_bonus":
                    ability.crit_bonus_pct += adj["amount"]
                elif adj["kind"] == "cast":
                    # Flat seconds come straight off the base cast; % cuts
                    # are treated divisively like the cast-speed stat. The
                    # original base is kept — the half-of-original floor.
                    if ability.orig_cast_secs is None:
                        ability.orig_cast_secs = ability.cast_secs
                    if adj["is_pct"]:
                        ability.cast_secs = ability.cast_secs / (1 + adj["amount"] / 100)
                    else:
                        ability.cast_secs = max(0.0, ability.cast_secs - adj["amount"])
                elif adj["kind"] == "recovery":
                    if adj["is_pct"]:
                        ability.recovery_secs = ability.recovery_secs / (1 + adj["amount"] / 100)
                    else:
                        ability.recovery_secs = max(0.0, ability.recovery_secs - adj["amount"])
                elif adj["kind"] == "reuse":
                    if ability.orig_recast_secs is None:
                        ability.orig_recast_secs = ability.recast_secs
                    if adj["is_pct"]:
                        ability.recast_secs = ability.recast_secs / (1 + adj["amount"] / 100)
                    else:
                        ability.recast_secs = max(0.0, ability.recast_secs - adj["amount"])
                elif adj["kind"] == "duration":
                    for c in ability.components:
                        if c.kind != "dot" or not c.duration_s or not c.interval_s:
                            continue
                        old_dur = c.duration_s
                        new_dur = old_dur * (1 + adj["amount"] / 100) if adj["is_pct"] else old_dur + adj["amount"]
                        new_dur = max(new_dur, 0.1)
                        c.interval_s = c.interval_s * (new_dur / old_dur)
                        c.duration_s = new_dur
                    if ability.duration_s:
                        ability.duration_s = max(
                            0.1,
                            ability.duration_s * (1 + adj["amount"] / 100)
                            if adj["is_pct"]
                            else ability.duration_s + adj["amount"],
                        )
                else:
                    continue
                ability.aa_adjustments.append(adj["line"])

    derived, set_passives = _derive_from_gear_sync(equip_counts, char_level)
    passives.extend(set_passives)
    for src in aa_sources:
        amt = parse_base_damage_bonus_pct([src.detail])
        derived.base_damage_bonus_pct += amt
        for school, flat in parse_school_damage_flat([src.detail]).items():
            derived.school_damage_flat[school] = derived.school_damage_flat.get(school, 0.0) + flat
        derived.sources.append(src)
    return out, passives, derived


class ClassBuffResponse(BaseModel):
    name: str
    base_name: str
    tier_name: str
    level: int
    target_scope: str  # 'group' | 'raid' | 'ally'
    icon_id: int | None = None
    icon_backdrop: int | None = None
    # Modelable stat mods parsed from the effect text (frontend BuffMods
    # keys — hastePct, dpsModPct, …). Unmapped stats stay in effect_lines.
    mods: dict[str, float] = {}
    # AA-granted buffs only: the spent rank and the node's max — spell
    # "tiers" on AA rows are ranks, so tier_name is blanked and the UI
    # labels "4/5" instead (nothing when the node has a single rank).
    rank: int | None = None
    max_rank: int | None = None
    # Pre-AA base recast — the half-of-original floor's reference (AA
    # cuts and reuse speed share the same cap).
    orig_recast_s: float | None = None
    procs: list[ProcResponse] = []
    effect_lines: list[str] = []
    # Set ⇒ a TEMP buff (the items catalogue duration / "Lasts for") the group member
    # rotates (duration + recast drive its windows); None ⇒ permanent.
    duration_s: float | None = None
    recast_s: float = 0.0
    # AA effect lines that adjusted this buff's reuse/duration (the
    # member's Enhance:/Focus: nodes) — provenance for the UI.
    aa_adjustments: list[str] = []


_SCOPE_LABEL = {"group": "group", "raid": "raid", "other": "ally"}

#: A stat-increase line, with the stat phrase captured.
_GENERIC_STAT_INCREASE_RE = re.compile(r"^Increases (?P<stat>[A-Za-z ,'-]+?) of [^.]*? by [\d,]+(?:\.\d+)?%?\.?$")

#: ALLOW-list: a buff earns a row only when its increase touches an
#: attribute (other than STA) or a combat stat. Anything not listed —
#: heals, wards, max health/power, regen, mitigation, resists, hate,
#: avoidance, illusion flavor — is irrelevant BY DEFAULT, with no
#: per-class or per-stat deny curation needed.
_RELEVANT_STAT_TERMS = (
    # attributes (STA deliberately absent)
    "str",
    "agi",
    "wis",
    "int",
    # combat stats
    "potency",
    "fervor",
    "haste",
    "attack speed",
    "dps",
    "multi attack",
    "double attack",
    "flurry",
    "crit chance",
    "crit bonus",
    "ability modifier",
    "casting speed",
    "reuse",
    "recovery",
    "spell damage",
    "combat art damage",
    "weapon damage",
    # offensive skills
    "slashing",
    "crushing",
    "piercing",
    "ranged",
    "disruption",
    "subjugation",
    "focus",
)

_RELEVANT_STAT_RES = tuple(re.compile(rf"\b{re.escape(t)}\b") for t in _RELEVANT_STAT_TERMS)


def _is_relevant_group_buff(mods: dict, procs: list[ProcResponse], lines: list[str]) -> bool:
    """The group panel's relevance filter: damage procs, or a stat line
    whose stat is on the allow-list (word-boundary matched, so 'STR and
    STA' qualifies via STR while a pure STA or Max Health line never
    does)."""
    if mods:
        return True
    if any(p.components for p in procs):
        return True
    for line in lines:
        m = _GENERIC_STAT_INCREASE_RE.match(line)
        if m is None:
            continue
        stat = m.group("stat").strip().lower()
        if any(rx.search(stat) for rx in _RELEVANT_STAT_RES):
            return True
    return False


def _buff_rows_to_responses(rows: list[SpellRow]) -> list[ClassBuffResponse]:
    """Beneficial group-scope spell rows → buff responses: stat mods +
    procs parsed from the effect text, the items catalogue duration join, relevance
    filter applied."""
    import json  # noqa: PLC0415

    meta = _items.spell_meta_by_names([f"{r.get('name')} ({r.get('tier_name')})" for r in rows])

    out: list[ClassBuffResponse] = []
    for r in rows:
        try:
            effects = json.loads(r.get("effects") or "[]")
        except (TypeError, ValueError):
            effects = []
        parsed = parse_effect_lines(effects)
        level = r.get("level") or 0
        m = meta.get(f"{r.get('name')} ({r.get('tier_name')})") or {}
        raw_duration = m.get("spell_duration")
        duration_s = (raw_duration / SPELL_DURATION_DIVISOR) if raw_duration else parsed["lasts_for_s"]
        # Until-cancelled concentration buffs (Velocity) carry their 1s
        # PULSE as the census duration — below the floor means "no real
        # duration": a PERMANENT buff, not a rotated temp.
        if duration_s is not None and duration_s < TEMP_BUFF_MIN_DURATION_S:
            duration_s = None
        # A recast at or under the duration means the buff is 100%
        # maintainable — functionally until-cancelled (Enraging Demeanor:
        # census says "60s" but recasts in 2s). Real rotated temps always
        # have recast > duration (CoB 49.6/20.6, Bolster 120/46).
        if duration_s is not None and float(r.get("recast_secs") or 0.0) <= duration_s:
            duration_s = None
        _, procs = _build_component_models(parsed, duration_s, level)
        base_line_name = _spells.strip_roman(r.get("name") or "")
        mods = apply_mod_key_overrides(base_line_name, parse_stat_mods(parsed["lines"]))
        display_lines = apply_text_overrides(base_line_name, parsed["lines"])
        if not _is_relevant_group_buff(mods, procs, parsed["lines"]):
            continue
        aa_rank = r.get("_aa_rank")
        out.append(
            ClassBuffResponse(
                name=r.get("name") or "",
                base_name=_spells.strip_roman(r.get("name") or ""),
                # AA rows: the spell "tier" is the spent RANK — blank the
                # research-tier label and carry rank/max instead.
                tier_name="" if aa_rank is not None else (r.get("tier_name") or ""),
                rank=aa_rank,
                max_rank=r.get("_aa_max_rank"),
                level=level,
                target_scope=_SCOPE_LABEL.get(r.get("target_type") or "", "group"),
                icon_id=r.get("icon_id"),
                icon_backdrop=r.get("icon_backdrop"),
                mods=mods,
                procs=procs,
                effect_lines=display_lines,
                duration_s=duration_s,
                recast_s=float(r.get("recast_secs") or 0.0),
            )
        )
    return out


def _class_buffs_sync(cls: str, max_level: int) -> list[ClassBuffResponse]:
    """SYNC (executor): a class's GENERIC group/raid/ally buff book —
    scroll universe (the items catalogue classes_json) joined to beneficial
    group-scope spells catalogue rows at the era's best assumed tiers."""
    names = _items.class_spell_names(cls)
    rows = _spells.beneficial_group_spells(sorted(names), max_level)
    return _buff_rows_to_responses(rows)


def _member_aa_adjustments_sync(aa_trees: list[tuple[int, dict[str, int]]]) -> list[AbilityAdjustment]:
    """A group member's spent AA nodes → per-ability reuse/duration
    modifiers (bard Enhance:/Focus: lines that shorten Cacophony of
    Blades' reuse, extend Perfection of the Maestro's duration, …)."""
    import json  # noqa: PLC0415

    out: list[AbilityAdjustment] = []
    for tree_id, spent in aa_trees:
        tree = _aas.get_tree(tree_id)
        if not tree:
            continue
        nodes = {n["node_id"]: n for n in tree["nodes"]}
        for node_id_str, rank in spent.items():
            if rank <= 0:
                continue
            try:
                node = nodes.get(int(node_id_str))
            except (TypeError, ValueError):
                continue
            crc = (node or {}).get("spellcrc") or 0
            if node is None or not crc or crc == _NO_SPELL_CRC:
                continue
            row = _spells.find_by_crc(crc, rank)
            if row is None:
                continue
            try:
                effects = json.loads(row.get("effects") or "[]")
            except (TypeError, ValueError):
                effects = []
            lines = [((e or {}).get("description") or "").strip() for e in effects]
            subject = aa_subject_of(row.get("name") or node.get("name") or "")
            out.extend(parse_ability_adjustments([ln for ln in lines if ln], subject))
    return out


def _apply_aa_adjustments(buffs: list[ClassBuffResponse], adjustments: list[AbilityAdjustment]) -> None:
    """Apply per-ability AA modifiers to the member's buffs IN PLACE,
    matched by base name. Reuse %: divisive like the reuse stat; reuse
    seconds: flat off the recast. Duration: additive seconds or %."""
    for b in buffs:
        base = b.base_name.lower()
        for adj in adjustments:
            if base not in adj["targets"]:
                continue
            if adj["kind"] == "reuse":
                if b.orig_recast_s is None:
                    b.orig_recast_s = b.recast_s
                if adj["is_pct"]:
                    b.recast_s = b.recast_s / (1 + adj["amount"] / 100)
                else:
                    b.recast_s = max(0.0, b.recast_s - adj["amount"])
            elif adj["kind"] == "duration" and b.duration_s:
                if adj["is_pct"]:
                    b.duration_s = b.duration_s * (1 + adj["amount"] / 100)
                else:
                    b.duration_s = max(0.0, b.duration_s + adj["amount"])
            elif adj["kind"] != "duration":
                continue  # 'damage' etc. — not meaningful on a stat buff
            b.aa_adjustments.append(adj["line"])


def _member_aa_buff_rows_sync(aa_trees: list[tuple[int, dict[str, int]]]) -> list[SpellRow]:
    """Group/raid-scope beneficial abilities GRANTED by spent AA nodes
    (Crusader-tree Fearless Morale: '+2% group potency'). Census spell
    lists OMIT AA-granted abilities entirely, so the member buff book
    must read the tree itself. find_by_crc(crc, rank) picks the earliest
    non-zero level band — the era-populated row (the lv-70 Fearless
    Morale carries the potency line; the lv-0/120+ bands are drifted to
    Fervor)."""
    out: list[SpellRow] = []
    for tree_id, spent in aa_trees:
        tree = _aas.get_tree(tree_id)
        if not tree:
            continue
        nodes = {n["node_id"]: n for n in tree["nodes"]}
        for node_id_str, rank in spent.items():
            if rank <= 0:
                continue
            try:
                node = nodes.get(int(node_id_str))
            except (TypeError, ValueError):
                continue
            crc = (node or {}).get("spellcrc") or 0
            if node is None or not crc or crc == _NO_SPELL_CRC:
                continue
            row = _spells.find_by_crc(crc, rank)
            # Raid-scope rows are deliberately excluded — raid-wide buffs
            # (Crusade, Heretic's Destruction) live in the Raid buffs
            # panel with its tier dropdown, not in the member book.
            if row is None or not row.get("beneficial") or row.get("target_type") != "group":
                continue
            # AA spell tiers are RANKS, not research tiers — carry the
            # spent rank (and the node's max) so the UI can label
            # "4/5" instead of a meaningless "Apprentice"/"Grandmaster".
            # cast: the rank annotations are rotation-private keys, not
            # the spells catalogue columns — SpellRow deliberately doesn't know them.
            out.append(cast(SpellRow, {**row, "_aa_rank": rank, "_aa_max_rank": int(node.get("maxtier") or 0) or None}))
    return out


def _character_buffs_sync(spell_ids: list[int], aa_trees: list[tuple[int, dict[str, int]]]) -> list[ClassBuffResponse]:
    """SYNC (executor): a SPECIFIC character's group/raid/ally buffs at
    the ranks and tiers they actually own (census spell list, PLUS
    group-scope buffs granted by their spent AA nodes) — best tier per
    exact name, then the highest-rank entry per base line — with their
    AA reuse/duration modifiers applied."""
    grouped: dict[str, SpellRow] = {}
    for r in _spells.find_by_ids(spell_ids).values():
        # 'raid' deliberately excluded: raid-wide buffs (Crusade) are the
        # Raid buffs panel's job — listing them here double-counts.
        if not r.get("beneficial") or r.get("target_type") not in ("group", "other"):
            continue
        if (r.get("level") or 0) <= 0:
            continue
        name = r.get("name") or ""
        prev = grouped.get(name)
        if prev is None or (r.get("tier") or 0) > (prev.get("tier") or 0):
            grouped[name] = r
    best = _spells.unique_highest_entries(list(grouped.values()))
    seen_names = {r.get("name") for r in best}
    best.extend(r for r in _member_aa_buff_rows_sync(aa_trees) if r.get("name") not in seen_names)
    buffs = _buff_rows_to_responses(best)
    _apply_aa_adjustments(buffs, _member_aa_adjustments_sync(aa_trees))
    return buffs


def _buff_tiers_sync(base_name: str, max_level: int) -> list[ClassBuffResponse]:
    return _buff_rows_to_responses(_spells.beneficial_buff_tiers(base_name, max_level))


@router.get("/simulator/buff-tiers", response_model=list[ClassBuffResponse])
@limiter.limit("60/minute")
async def get_buff_tiers(request: Request, name: str) -> list[ClassBuffResponse]:
    """Era tier rows (Apprentice → Master) of one raid-buff line, parsed —
    the raid-buff panel's tier dropdown (default Expert). ``name`` is the
    base spell name without rank ('Crusade')."""
    if not _spells.ready():
        raise HTTPException(status_code=503, detail="Spells database not available")
    max_level = current_server().max_level or 80
    return await run_sync(_buff_tiers_sync, name, max_level)


@router.get("/simulator/class-buffs", response_model=list[ClassBuffResponse])
@limiter.limit("60/minute")
async def get_class_buffs(request: Request, cls: str) -> list[ClassBuffResponse]:
    """A class's group/raid/ally buff book for the simulator's group
    make-up panel — real spell names with parsed mods and procs."""
    if not _spells.ready():
        raise HTTPException(status_code=503, detail="Spells database not available")
    max_level = current_server().max_level or 80
    return await run_sync(_class_buffs_sync, cls, max_level)


class MemberStatsResponse(BaseModel):
    """The stat snapshot buff-granted PROC damage scales from — the
    SUPPLIER's chain, not the receiver's (PotM's Precise Note hits with
    the bard's stats). Field names mirror the frontend SimStats keys so
    the engine can use this directly."""

    primary_stat: float = 0.0  # archetype attribute (scout AGI, …)
    potency: float = 0.0
    ability_mod: float = 0.0
    crit_chance: float = 0.0
    crit_bonus: float = 0.0
    fervor: float = 0.0
    base_damage_bonus_pct: float = 0.0  # their gear/set derivation


class CharacterBuffsResponse(BaseModel):
    character_name: str
    cls: str | None = None
    level: int | None = None
    buffs: list[ClassBuffResponse] = []
    stats: MemberStatsResponse = MemberStatsResponse()


@router.get("/simulator/character-buffs", response_model=CharacterBuffsResponse)
@limiter.limit("60/minute")
async def get_character_buffs(request: Request, name: str) -> CharacterBuffsResponse:
    """A specific group member's buff book at THEIR owned spell ranks —
    the group make-up panel adds real characters, not generic classes,
    so buff values reflect what that player can actually cast."""
    if not _spells.ready():
        raise HTTPException(status_code=503, detail="Spells database not available")
    char = await _resolve_character(name)
    aa_trees = await _fetch_aa_trees(name)
    buffs = await run_sync(_character_buffs_sync, char.spell_ids or [], aa_trees)

    # The member's recast cadence runs on THEIR reuse-speed stat
    # (Ariadneh's CoB reads 49.6s in game vs the 60s base), floored at
    # HALF the original base like every reuse reduction.
    member_reuse = float((char.stats.reuse_speed if char.stats else None) or 0.0)
    if member_reuse > 0:
        for b in buffs:
            if b.recast_s > 0:
                floor = (b.orig_recast_s or b.recast_s) / 2
                b.recast_s = max(floor, b.recast_s / (1 + min(member_reuse, 100.0) / 100))

    # The member's proc-scaling stat snapshot: sheet stats + their own
    # gear/set base-damage derivation. Primary attribute by archetype.
    equip_counts: dict[int, int] = {}
    for slot in char.equipment or []:
        if slot.item_id and str(slot.item_id).isdigit():
            equip_counts[int(slot.item_id)] = equip_counts.get(int(slot.item_id), 0) + 1
        for adorn in slot.adorn_slots or []:
            if adorn.adorn_id and str(adorn.adorn_id).isdigit():
                equip_counts[int(adorn.adorn_id)] = equip_counts.get(int(adorn.adorn_id), 0) + 1
    derived, _set_passives = await run_sync(_derive_from_gear_sync, equip_counts, char.level or 80)
    s = char.stats
    cls = char.cls or ""
    primary = 0.0
    if s is not None:
        if cls in PRIESTS:
            primary = float(s.wis_eff or 0)
        elif cls in MAGES:
            primary = float(s.int_eff or 0)
        elif cls in SCOUTS:
            primary = float(s.agi_eff or 0)
        elif cls in FIGHTERS:
            primary = float(s.str_eff or 0)
    stats = MemberStatsResponse(
        primary_stat=primary,
        potency=float((s and s.potency) or 0.0),
        ability_mod=float((s and s.ability_mod) or 0.0),
        crit_chance=float((s and s.crit_chance) or 0.0),
        crit_bonus=float((s and s.crit_bonus) or 0.0),
        fervor=float((s and s.fervor) or 0.0),
        base_damage_bonus_pct=derived.base_damage_bonus_pct,
    )
    return CharacterBuffsResponse(character_name=char.name, cls=char.cls, level=char.level, buffs=buffs, stats=stats)


async def _fetch_aa_trees(name: str) -> list[tuple[int, dict[str, int]]]:
    """The character's spent AA nodes per tree, via the AA route's full
    read path (cache → store → live). Soft-fails to [] — AA abilities
    are additive; their absence must never break the rotation payload."""
    from backend.server.api.aa import get_character_aas  # noqa: PLC0415 — sibling router, avoid import cycle

    try:
        aas = await get_character_aas(name)
    except HTTPException:
        return []
    except Exception as exc:
        _log.warning("[rotation] AA fetch failed for %s: %s", name, exc)
        return []
    return [(t.tree_id, {k: v for k, v in t.spent.items()}) for t in aas.trees]


async def _resolve_character(name: str):
    """Store-first character resolution — the character page's read path
    (hot cache → census store with background refresh → one live fetch),
    so a cold cache or a down Census never blocks the simulator."""
    return await resolve_character_store_first(name)


@router.get("/character/{name}/rotation-data", response_model=CharacterRotationDataResponse)
@limiter.limit("30/minute")
async def get_character_rotation_data(request: Request, name: str) -> CharacterRotationDataResponse:
    """The rotation simulator's per-character ability payload."""
    if not _spells.ready():
        raise HTTPException(status_code=503, detail="Spells database not available")

    char = await _resolve_character(name)
    aa_trees = await _fetch_aa_trees(name)

    # Worn items + their adorns, weighted by multiplicity (two of the same
    # ring = double the bonus) — the derivation universe.
    equip_counts: dict[int, int] = {}
    for slot in char.equipment or []:
        if slot.item_id and str(slot.item_id).isdigit():
            iid = int(slot.item_id)
            equip_counts[iid] = equip_counts.get(iid, 0) + 1
        for adorn in slot.adorn_slots or []:
            if adorn.adorn_id and str(adorn.adorn_id).isdigit():
                aid = int(adorn.adorn_id)
                equip_counts[aid] = equip_counts.get(aid, 0) + 1

    abilities, passives, derived = await run_sync(
        _build_abilities_sync, char.spell_ids or [], aa_trees, equip_counts, char.level or 80
    )
    return CharacterRotationDataResponse(
        character_name=char.name,
        cls=char.cls,
        level=char.level,
        abilities=abilities,
        passives=passives,
        derived=derived,
    )
