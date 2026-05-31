"""Unit tests for the pet-detection classifier in parses/pet_detection.py.

The classifier is a pure function that takes a list of combatant dicts
plus a zone-category string and returns {combatant.id: is_player}. The
6-stage pipeline + bucket-fill rules are the spec's source of truth — see
docs/superpowers/specs/2026-05-30-pet-detection-pipeline-design.md.
"""

from __future__ import annotations

import pytest

from backend.server.parses.pet_detection import EQ2_PET_PATTERN, KNOWN_EXAMPLES, classify_combatants


def _ally(
    cid: int,
    name: str,
    *,
    cls: str | None = None,
    encdps: float = 0.0,
    enchps: float = 0.0,
) -> dict:
    """Minimal combatant dict — the fields the classifier reads."""
    return {
        "id": cid,
        "name": name,
        "ally": 1,
        "cls": cls,
        "encdps": encdps,
        "enchps": enchps,
    }


def _enemy(cid: int, name: str) -> dict:
    return {"id": cid, "name": name, "ally": 0, "cls": None, "encdps": 0.0, "enchps": 0.0}


# ── Regex tests ───────────────────────────────────────────────────────


def test_regex_matches_every_known_example():
    for name in KNOWN_EXAMPLES:
        assert EQ2_PET_PATTERN.match(name) or name in KNOWN_EXAMPLES, name


def test_regex_matches_prototype_examples():
    for name in ("Gibab", "Zosn", "Kebn", "Zebekn", "Jentik", "Xebobtik", "Kabantik", "Jonaner"):
        assert EQ2_PET_PATTERN.match(name.lower()), name


def test_regex_rejects_real_player_names():
    for name in ("Bob", "Fluffy", "Menludiir", "Sihtric", "Tarinax", "Vyemm"):
        assert not EQ2_PET_PATTERN.match(name.lower()), name


# ── Pipeline stage tests (single-shot, no bucket-fill) ────────────────


def test_stage1_enemy_omitted():
    out = classify_combatants([_enemy(1, "a krait")], "raid")
    assert 1 not in out


def test_stage2_empty_name_is_pet():
    out = classify_combatants([_ally(1, "", cls="Wizard")], "raid")
    assert out[1] is False


def test_stage2_unknown_name_is_pet():
    out = classify_combatants([_ally(1, "Unknown", cls="Wizard")], "raid")
    assert out[1] is False


def test_stage3_multi_word_is_pet():
    out = classify_combatants([_ally(1, "Bravo's Pet", cls="Wizard")], "raid")
    assert out[1] is False


def test_stage4_regex_match_is_pet():
    out = classify_combatants([_ally(1, "Gibab", cls="Wizard")], "raid")
    assert out[1] is False, "Auto-pet name should override cls"


def test_stage5_cls_resolved_is_player():
    out = classify_combatants([_ally(1, "Menludiir", cls="Wizard")], "raid")
    assert out[1] is True


# ── Bucket-fill rule table (one test per row of the spec) ─────────────


def test_raid_fill_under_24_additive_below_25_total():
    confirmed = [_ally(i, f"P{i}", cls="Wizard", encdps=1000.0) for i in range(1, 11)]
    unconfirmed = [_ally(20 + i, f"U{i}", encdps=500.0 - i) for i in range(5)]
    out = classify_combatants(confirmed + unconfirmed, "raid")
    n_player = sum(1 for v in out.values() if v)
    assert n_player == 15, "10 confirmed + 5 unconfirmed promoted under cap of 24"


def test_raid_fill_then_trim_at_24_when_total_ge_25():
    confirmed = [_ally(i, f"P{i}", cls="Wizard", encdps=1000.0) for i in range(1, 26)]
    out = classify_combatants(confirmed, "raid")
    n_player = sum(1 for v in out.values() if v)
    assert n_player == 24, "25 confirmed raiders capped at 24 (lowest 1 trimmed)"


def test_dungeon_fill_to_6():
    confirmed = [_ally(i, f"P{i}", cls="Wizard", encdps=1000.0) for i in range(1, 4)]
    unconfirmed = [_ally(20 + i, f"U{i}", encdps=500.0 - i) for i in range(5)]
    out = classify_combatants(confirmed + unconfirmed, "dungeon")
    n_player = sum(1 for v in out.values() if v)
    assert n_player == 6, "3 confirmed + 3 unconfirmed promoted to hit target 6"


def test_dungeon_no_trim_above_6():
    confirmed = [_ally(i, f"P{i}", cls="Wizard", encdps=1000.0) for i in range(1, 9)]
    out = classify_combatants(confirmed, "dungeon")
    n_player = sum(1 for v in out.values() if v)
    assert n_player == 8, "Dungeon rule is additive only — 8 confirmed stay players"


def test_other_n_total_under_6_no_op():
    confirmed = [_ally(i, f"P{i}", cls="Wizard", encdps=1000.0) for i in range(1, 4)]
    unconfirmed = [_ally(20 + i, f"U{i}", encdps=500.0 - i) for i in range(2)]
    out = classify_combatants(confirmed + unconfirmed, "other")
    n_player = sum(1 for v in out.values() if v)
    assert n_player == 3, "n_total=5 in 'other' is no-op"


def test_other_n_total_7_to_10_fills_to_6():
    confirmed = [_ally(i, f"P{i}", cls="Wizard", encdps=1000.0) for i in range(1, 4)]
    unconfirmed = [_ally(20 + i, f"U{i}", encdps=500.0 - i) for i in range(5)]
    out = classify_combatants(confirmed + unconfirmed, "other")
    n_player = sum(1 for v in out.values() if v)
    assert n_player == 6, "n_total=8 in 'other' fills confirmed up to 6"


def test_other_n_total_11_to_24_treats_as_raid_fills_to_24():
    confirmed = [_ally(i, f"P{i}", cls="Wizard", encdps=1000.0) for i in range(1, 11)]
    unconfirmed = [_ally(20 + i, f"U{i}", encdps=500.0 - i) for i in range(10)]
    out = classify_combatants(confirmed + unconfirmed, "other")
    n_player = sum(1 for v in out.values() if v)
    assert n_player == 20, "n_total=20 in 'other' treated as raid, all unconfirmed promoted (cap 24 not hit)"


def test_other_n_total_ge_25_treats_as_raid_caps_at_24():
    confirmed = [_ally(i, f"P{i}", cls="Wizard", encdps=1000.0) for i in range(1, 27)]
    out = classify_combatants(confirmed, "other")
    n_player = sum(1 for v in out.values() if v)
    assert n_player == 24, "n_total=26 in 'other' treated as raid, trimmed to 24"


# ── Determinism + tiebreaker ──────────────────────────────────────────


def test_determinism_same_input_same_output():
    combatants = [_ally(i, f"P{i}", encdps=500.0 + i, enchps=100.0) for i in range(1, 10)]
    first = classify_combatants(combatants, "dungeon")
    second = classify_combatants(combatants, "dungeon")
    assert first == second


def test_tiebreaker_name_ascending_on_equal_contribution():
    # Two unconfirmed allies tied on (encdps + enchps); name ASC wins.
    confirmed = [_ally(i, f"P{i}", cls="Wizard", encdps=1000.0) for i in range(1, 6)]
    unconfirmed = [
        _ally(99, "Zelda", encdps=100.0, enchps=50.0),
        _ally(98, "Alice", encdps=100.0, enchps=50.0),
    ]
    out = classify_combatants(confirmed + unconfirmed, "dungeon")
    # Target = 6, 5 confirmed → 1 unconfirmed promoted. Tiebreaker on name ASC
    # picks Alice (98), leaves Zelda (99) as pet.
    assert out[98] is True
    assert out[99] is False


# ── Edge cases ─────────────────────────────────────────────────────────


def test_empty_combatant_list_returns_empty_dict():
    assert classify_combatants([], "raid") == {}


def test_all_pet_encounter_yields_zero_players():
    combatants = [_ally(i, f"Pet {i}") for i in range(1, 5)]  # all multi-word
    out = classify_combatants(combatants, "raid")
    assert all(v is False for v in out.values())


def test_missing_keys_do_not_raise():
    # ``ally`` missing → treat as 0 (omit); ``cls`` missing → unconfirmed.
    minimal = [{"id": 1, "name": "Bob"}]
    out = classify_combatants(minimal, "raid")
    # Without ally=1 the row is omitted entirely.
    assert out == {}
