"""Effect-text parser tests — table-driven over real spells.db lines."""

from __future__ import annotations

import pytest

from backend.eq2db.spell_effects import parse_damage_line, parse_effect_lines


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
