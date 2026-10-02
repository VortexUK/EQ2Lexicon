"""Effect-text parser tests — table-driven over real spells.db lines."""

from __future__ import annotations

import pytest

from backend.eq2db.spell_effects import (
    aa_subject_of,
    parse_ability_adjustments,
    parse_damage_line,
    parse_effect_lines,
    parse_stat_mods,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Range hit (the common melee/spell line).
        (
            "Inflicts 447 - 746 melee damage on target",
            [{"kind": "hit", "min_dmg": 447.0, "max_dmg": 746.0, "school": "melee", "target_scope": "single"}],
        ),
        # Flat hit, trailing period.
        (
            "Inflicts 10,843 disease damage on target.",
            [{"kind": "hit", "min_dmg": 10843.0, "max_dmg": 10843.0, "school": "disease", "target_scope": "single"}],
        ),
        # Comma thousands + range.
        (
            "Inflicts 56,925 - 105,719 cold damage on target.",
            [{"kind": "hit", "min_dmg": 56925.0, "max_dmg": 105719.0, "school": "cold", "target_scope": "single"}],
        ),
        # AoE scope.
        (
            "Inflicts 1,200 heat damage on targets in Area of Effect.",
            [{"kind": "hit", "min_dmg": 1200.0, "max_dmg": 1200.0, "school": "heat", "target_scope": "aoe"}],
        ),
        # Encounter scope.
        (
            "Inflicts 300 - 500 magic damage on target encounter.",
            [{"kind": "hit", "min_dmg": 300.0, "max_dmg": 500.0, "school": "magic", "target_scope": "encounter"}],
        ),
        # Pure DoT with numeric interval.
        (
            "Inflicts 210 poison damage on target every 4 seconds.",
            [
                {
                    "kind": "dot",
                    "min_dmg": 210.0,
                    "max_dmg": 210.0,
                    "school": "poison",
                    "target_scope": "single",
                    "interval_s": 4.0,
                    "duration_s": None,
                }
            ],
        ),
        # Decimal interval.
        (
            "Inflicts 95 - 130 mental damage on target every 2.5 seconds.",
            [
                {
                    "kind": "dot",
                    "min_dmg": 95.0,
                    "max_dmg": 130.0,
                    "school": "mental",
                    "target_scope": "single",
                    "interval_s": 2.5,
                    "duration_s": None,
                }
            ],
        ),
        # Bare "every second" → interval 1.0.
        (
            "Inflicts 88 divine damage on target every second.",
            [
                {
                    "kind": "dot",
                    "min_dmg": 88.0,
                    "max_dmg": 88.0,
                    "school": "divine",
                    "target_scope": "single",
                    "interval_s": 1.0,
                    "duration_s": None,
                }
            ],
        ),
        # "instantly and every" → hit + dot with the same values.
        (
            "Inflicts 512 - 855 cold damage on target instantly and every 4 seconds",
            [
                {"kind": "hit", "min_dmg": 512.0, "max_dmg": 855.0, "school": "cold", "target_scope": "single"},
                {
                    "kind": "dot",
                    "min_dmg": 512.0,
                    "max_dmg": 855.0,
                    "school": "cold",
                    "target_scope": "single",
                    "interval_s": 4.0,
                    "duration_s": None,
                },
            ],
        ),
    ],
)
def test_parse_damage_line_positive(text, expected):
    assert parse_damage_line(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Heals caster for 2,856.",
        "Increases Threat to target by 1,024 - 1,252",
        "Decreases Mitigation of target vs elemental damage by 810.",
        "Applies Knockdown on termination.  Lasts for 2.4 seconds.",
        "Inflicts 100.0% of max health in crushing damage on target.",  # special text, no absolute numbers
        "Inflicts up to 40 times the listed damage, based on the amount of increments of Focused Casting.",
        "Stuns target",
        "",
    ],
)
def test_parse_damage_line_negative(text):
    assert parse_damage_line(text) is None


def test_parse_effect_lines_full_spell():
    """A Dark Pyre-shaped spell: instant line + separate DoT line."""
    effects = [
        {"description": "Inflicts 902 - 1,676 heat damage on target", "indentation": 0},
        {"description": "Inflicts 210 heat damage on target every 4 seconds", "indentation": 0},
        {"description": "Decreases Mitigation of target vs elemental damage by 810.", "indentation": 0},
    ]
    p = parse_effect_lines(effects)
    kinds = [c["kind"] for c in p["components"]]
    assert kinds == ["hit", "dot"]
    assert p["unparsed_damage"] == []
    assert len(p["lines"]) == 3


def test_indented_damage_is_conditional_not_modeled():
    """Proc sub-effects (indented) never become components — they're
    surfaced in unparsed_damage so the UI can badge them."""
    effects = [
        {"description": "When target is damaged this spell has a 20% chance to cast Backlash.", "indentation": 0},
        {"description": "Inflicts 300 - 500 magic damage on target", "indentation": 1},
    ]
    p = parse_effect_lines(effects)
    assert p["components"] == []
    assert p["unparsed_damage"] == ["Inflicts 300 - 500 magic damage on target"]


def test_unparseable_top_level_damage_is_flagged():
    effects = [{"description": "Inflicts 100.0% of max health in crushing damage on target.", "indentation": 0}]
    p = parse_effect_lines(effects)
    assert p["components"] == []
    assert p["unparsed_damage"] == ["Inflicts 100.0% of max health in crushing damage on target."]


def test_condition_after_layout_attaches_condition():
    """Divine Strike layout: the conditional duplicate line sits at
    indentation 0 with its 'If ...' condition AFTER it, one level deeper.
    It must become a conditional component — not an unconditional
    double-count, not unparsed."""
    effects = [
        {"description": "Inflicts 754 - 922 divine damage on target.", "indentation": 0},
        {"description": "Inflicts 754 - 922 divine damage on target.", "indentation": 0},
        {"description": "If target is undead", "indentation": 1},
    ]
    p = parse_effect_lines(effects)
    assert [c.get("condition") for c in p["components"]] == [None, "If target is undead"]
    assert p["unparsed_damage"] == []


def test_condition_trailing_period_stripped():
    effects = [
        {"description": "Inflicts 100 poison damage on target every 2 seconds", "indentation": 0},
        {"description": "If target is bleeding.", "indentation": 1},
    ]
    p = parse_effect_lines(effects)
    (dot,) = p["components"]
    assert dot["condition"] == "If target is bleeding"


def test_condition_applies_to_both_hit_and_dot():
    effects = [
        {"description": "Inflicts 512 - 855 cold damage on target instantly and every 4 seconds", "indentation": 0},
        {"description": "If target is elemental", "indentation": 1},
    ]
    p = parse_effect_lines(effects)
    assert [c["condition"] for c in p["components"]] == ["If target is elemental"] * 2


def test_deeper_non_if_follower_is_not_a_condition():
    effects = [
        {"description": "Inflicts 447 - 746 melee damage on target", "indentation": 0},
        {"description": "This effect cannot be critically applied.", "indentation": 1},
    ]
    p = parse_effect_lines(effects)
    (comp,) = p["components"]
    assert comp["condition"] is None


def test_proc_block_parsed_bolt_of_power():
    """AA passive proc layout: top-level trigger line, proc effects
    indented below. The damage becomes a proc entry — not a component,
    not unparsed."""
    effects = [
        {
            "description": "On any combat or spell hit this spell has a 50% chance to cast Bolt of Power on target of attack.",
            "indentation": 0,
        },
        {"description": "Interrupts target", "indentation": 1},
        {"description": "Inflicts 89 - 149 divine damage on target.", "indentation": 1},
    ]
    p = parse_effect_lines(effects)
    assert p["components"] == []
    assert p["unparsed_damage"] == []
    (proc,) = p["procs"]
    assert proc["trigger"] == "any_hit"
    assert proc["chance_pct"] == 50.0
    assert proc["name"] == "Bolt of Power"
    (c,) = proc["components"]
    assert (c["min_dmg"], c["max_dmg"], c["school"]) == (89.0, 149.0, "divine")


def test_proc_will_cast_means_100pct_and_other_triggers():
    effects = [
        {"description": "On a melee hit this spell will cast Blade Chime on target of attack.", "indentation": 0},
        {"description": "Inflicts 621 - 1,036 disease damage on target", "indentation": 1},
        {"description": "On a hostile spell cast this spell has a 25% chance to cast Backfire.", "indentation": 0},
        {"description": "Inflicts 200 magic damage on target", "indentation": 1},
    ]
    p = parse_effect_lines(effects)
    assert [(x["trigger"], x["chance_pct"]) for x in p["procs"]] == [("melee_hit", 100.0), ("spell_cast", 25.0)]


def test_proc_without_damage_is_dropped_and_block_ends_at_top_level():
    effects = [
        {"description": "On a hit this spell will cast Stifling Blow on target of attack.", "indentation": 0},
        {"description": "Stifles target", "indentation": 1},
        {"description": "Inflicts 300 - 500 heat damage on target", "indentation": 0},  # back at top level
    ]
    p = parse_effect_lines(effects)
    assert p["procs"] == []  # no damage inside the proc block
    (comp,) = p["components"]  # the top-level line after the block is normal
    assert comp["min_dmg"] == 300.0


def test_proc_trigger_with_trailing_sentence_still_matches():
    """Aria-of-Magic layout: the trigger line carries extra sentences
    ("This effect normalizes based off of a three second triggering
    interval.") after the cast clause — they must not break the match."""
    effects = [
        {
            "description": "On a hostile spell cast this spell has a 15% chance to cast Aria of Magic on target of spell.  This effect normalizes based off of a three second triggering interval.",
            "indentation": 0,
        },
        {"description": "Inflicts 250 - 305 mental damage on target", "indentation": 1},
    ]
    p = parse_effect_lines(effects)
    (proc,) = p["procs"]
    assert proc["trigger"] == "spell_cast" and proc["chance_pct"] == 15.0
    assert proc["name"] == "Aria of Magic"


def test_parse_stat_mods_maps_modelable_stats():
    lines = [
        "Increases Haste of group members (AE) by 63.6.",
        "Increases Multi Attack of group members (AE) by 35.3.",
        "Increases DPS of raid and group members (AE) by 24.2.",
        "Increases Crit Chance of target by 5.0.",
        "Increases STR and AGI of group members (AE) by 96.6.",  # attr flats (both)
        "Increases Mitigation of group members (AE) vs noxious damage by 480.",  # unmapped shape
        "Heals group members (AE) for 100.",  # not a stat line
    ]
    mods = parse_stat_mods(lines)
    assert mods == {
        "hastePct": 63.6,
        "doubleAttackPct": 35.3,
        "dpsModPct": 24.2,
        "critChancePct": 5.0,
        "strFlat": 96.6,
        "agiFlat": 96.6,
    }


def test_parse_stat_mods_sums_duplicates_and_handles_commas():
    lines = [
        "Increases Haste of group members (AE) by 10.",
        "Increases Haste of caster by 1,000.5.",
    ]
    assert parse_stat_mods(lines) == {"hastePct": 1010.5}


def test_applies_pulse_wrapper_exorcise_shape():
    """'Applies X instantly and every N seconds.' + indented Inflicts =
    a self-applied pulse AoE (Exorcise/Consecrate/Open Wounds): the hit
    lands immediately AND ticks on the wrapper's cadence."""
    effects = [
        {"description": "Applies Exorcise instantly and every 6 seconds.", "indentation": 0},
        {"description": "Inflicts 269 - 448 divine damage on targets in Area of Effect.", "indentation": 1},
        {"description": "Reduces the potency of heals by 95%.", "indentation": 0},
    ]
    p = parse_effect_lines(effects)
    assert p["unparsed_damage"] == []
    hit, dot = p["components"]
    assert hit["kind"] == "hit" and (hit["min_dmg"], hit["max_dmg"]) == (269.0, 448.0)
    assert hit["target_scope"] == "aoe"
    assert dot["kind"] == "dot" and dot["interval_s"] == 6.0 and dot["duration_s"] is None


def test_applies_pulse_wrapper_with_lasts_for():
    """Netherealm shape: the wrapper carries its own duration."""
    effects = [
        {
            "description": "Applies Netherous Cascade instantly and every 5 seconds.  Lasts for 15.0 seconds.",
            "indentation": 0,
        },
        {"description": "Inflicts 300 - 366 poison damage on target.", "indentation": 1},
    ]
    p = parse_effect_lines(effects)
    hit, dot = p["components"]
    assert hit["kind"] == "hit"
    assert dot["kind"] == "dot" and dot["interval_s"] == 5.0 and dot["duration_s"] == 15.0


def test_applies_without_every_clause_is_not_a_pulse():
    effects = [
        {"description": "Applies Knockdown on termination.", "indentation": 0},
        {"description": "Inflicts 300 - 500 magic damage on target", "indentation": 1},
    ]
    p = parse_effect_lines(effects)
    assert p["components"] == []
    assert p["unparsed_damage"] == ["Inflicts 300 - 500 magic damage on target"]


def test_defensive_trigger_parses_as_when_damaged():
    """Damage-shield procs (Thorns shapes, and the Templar Mythical's
    Divine Light → Shock of Light) parse with the 'when_damaged' trigger;
    the engine rates them from the user-set incoming-hits knob."""
    effects = [
        {"description": "When damaged with a melee weapon this spell will cast Thorns.", "indentation": 0},
        {"description": "Inflicts 120 piercing damage on target", "indentation": 1},
    ]
    p = parse_effect_lines(effects)
    (proc,) = p["procs"]
    assert proc["trigger"] == "when_damaged" and proc["chance_pct"] == 100.0
    assert p["unparsed_damage"] == []


def test_divine_light_shape_when_damaged_proc():
    effects = [
        {"description": "When damaged this spell will cast Shock of Light on target's attacker.", "indentation": 0},
        {"description": "Inflicts 1,866 - 2,281 divine damage on target.", "indentation": 1},
        {"description": "This effect can only trigger once every 0.1 seconds.", "indentation": 1},
        {"description": "Reduces all damage done to the target by 8%.", "indentation": 0},
    ]
    p = parse_effect_lines(effects)
    (proc,) = p["procs"]
    assert proc["trigger"] == "when_damaged" and proc["name"] == "Shock of Light"
    (c,) = proc["components"]
    assert (c["min_dmg"], c["max_dmg"]) == (1866.0, 2281.0)


def test_lasts_for_captured():
    effects = [
        {"description": "Inflicts 100 heat damage on target every 2 seconds", "indentation": 0},
        {"description": "Lasts for 12.0 seconds.", "indentation": 0},
    ]
    p = parse_effect_lines(effects)
    assert p["lasts_for_s"] == 12.0


def test_empty_and_none_effects():
    assert parse_effect_lines([])["components"] == []
    assert parse_effect_lines([{"description": None, "indentation": 0}])["lines"] == []


def test_ability_adjustments_explicit_targets():
    adj = parse_ability_adjustments(["Reduces the reuse time of Cacophony of Blades by 30 seconds."])
    assert adj == [
        {
            "targets": ["cacophony of blades"],
            "kind": "reuse",
            "amount": 30.0,
            "is_pct": False,
            "line": "Reduces the reuse time of Cacophony of Blades by 30 seconds.",
        }
    ]


def test_ability_adjustments_multi_target_and_subject():
    adj = parse_ability_adjustments(
        [
            "Increases duration of Cacophony of Blades and Peal Of Battle by 3 seconds.",
            "Improves duration by 10 seconds.",  # self-referential -> subject
            "Improves the reuse speed of Precision by 20%.",
            "Increases effectiveness of Perfection of the Maestro by 10.",  # not a timing line
        ],
        subject="Perfection of the Maestro",
    )
    assert [a["kind"] for a in adj] == ["duration", "duration", "reuse"]
    assert adj[0]["targets"] == ["cacophony of blades", "peal of battle"]
    assert adj[1]["targets"] == ["perfection of the maestro"] and adj[1]["amount"] == 10.0
    assert adj[2]["is_pct"] is True and adj[2]["amount"] == 20.0


def test_ability_adjustments_no_subject_self_line_dropped():
    assert parse_ability_adjustments(["Improves reuse speed by 30 seconds"]) == []


def test_aa_subject_of():
    assert aa_subject_of("Enhance: Cacophony of Blades") == "Cacophony of Blades"
    assert aa_subject_of("Focus: Exorcise") == "Exorcise"
    assert aa_subject_of("Bolt of Power") is None


def test_target_cast_proc_with_trigger_budget():
    """Slothful Spirit shape: a hostile debuff whose proc fires on the
    TARGET's spell casts, with a fixed per-application trigger budget."""
    effects = [
        {"description": "Target will lose 80% more power when power is consumed.", "indentation": 0},
        {"description": "On a spell cast this spell will cast Sloth's Habitat on target.", "indentation": 0},
        {"description": "Inflicts 420 - 513 divine damage on target.", "indentation": 1},
        {"description": "Grants a total of 3 triggers of the spell.", "indentation": 1},
    ]
    parsed = parse_effect_lines(effects)
    (proc,) = parsed["procs"]
    assert proc["trigger"] == "target_cast"
    assert proc["trigger_count"] == 3.0
    assert proc["name"] == "Sloth's Habitat"
    (c,) = proc["components"]
    assert (c["min_dmg"], c["max_dmg"]) == (420.0, 513.0)


def test_parse_stat_mods_attributes_and_compound_phrases():
    """Attribute buffs map to <attr>Flat keys; compound phrases grant the
    amount to each listed stat; STA stays unmapped."""
    mods = parse_stat_mods(
        [
            "Increases WIS of group members (AE) by 53.5.",
            "Increases AGI, STR and STA of target by 73.6.",
            "Increases Haste of group members (AE) by 30.5.",
        ]
    )
    assert mods == {"wisFlat": 53.5, "agiFlat": 73.6, "strFlat": 73.6, "hastePct": 30.5}


def test_parse_stat_mods_recovery_speed_grammars():
    """Recovery speed: the stat-line form ("Increases Ability Recovery
    Speed of caster by 48%") and the ally-buff form (Time Compression:
    "Improves recovery speed of spells by 40%."). The per-ability
    Enhance-Jab shape (no "of spells") must NOT match."""
    assert parse_stat_mods(["Increases Ability Recovery Speed of caster by 48.0%"]) == {"recoverySpeedPct": 48.0}
    assert parse_stat_mods(["Improves recovery speed of spells by 40%."]) == {"recoverySpeedPct": 40.0}
    assert parse_stat_mods(["Improves casting and recovery speed of spells by 30%"]) == {
        "recoverySpeedPct": 30.0,
        "castSpeedPct": 30.0,
    }
    assert parse_stat_mods(["Improves casting and recovery speed by 60%."]) == {}
