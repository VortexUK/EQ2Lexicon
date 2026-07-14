"""Tests for the gear-set stat-delta approximation (stat_deltas.py).

Seeds a temp items.db (items + item_stats) and re-points the shared items
catalogue instance at it — the eq2db test convention.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.eq2db import items as items_mod
from backend.server.api.character import stat_deltas
from backend.server.api.character.stat_deltas import compute_stat_deltas, stat_totals

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _slot(item_id: str | None, adorn_ids: tuple[str, ...] = ()) -> SimpleNamespace:
    """Duck-typed EquipmentSlotResponse — stat_deltas only reads item_id + adorn_slots."""
    return SimpleNamespace(
        item_id=item_id,
        adorn_slots=[SimpleNamespace(adorn_id=a) for a in adorn_ids],
    )


@pytest.fixture
def items_db(tmp_path, monkeypatch):
    """Temp items.db with a small stat + set-bonus fixture; the shared
    catalogue instance is re-pointed at it for the test."""
    db_path = tmp_path / "items.db"
    cat = items_mod.ItemCatalogue(db_path)
    conn = cat.init_db()

    def add_item(item_id: int, name: str, *, setbonus_name: str | None = None, raw: dict | None = None):
        conn.execute(
            "INSERT INTO items (id, displayname, displayname_lower, setbonus_name, raw_json) VALUES (?, ?, ?, ?, ?)",
            (item_id, name, name.lower(), setbonus_name, json.dumps(raw) if raw else None),
        )

    def add_stat(item_id: int, stat: str, value: float):
        conn.execute("INSERT INTO item_stats (item_id, stat, value) VALUES (?, ?, ?)", (item_id, stat, value))

    # Plain items
    add_item(1, "Worn Helm")
    add_stat(1, "Potency", 10.0)
    add_stat(1, "Stamina", 30.0)
    add_item(2, "Set Helm A")
    add_stat(2, "Potency", 16.0)
    add_item(3, "All-Stats Ring")
    add_stat(3, "Primary Attributes", 25.0)
    add_item(4, "White Adorn")
    add_stat(4, "Crit Bonus", 2.5)
    add_item(5, "Skill Item")
    add_stat(5, "Resolve", 50.0)  # unmapped → ignored
    add_stat(5, "Combat Skills", 11.0)  # unmapped → ignored

    # A 3-piece armor set: 2-piece tier gives sta, 5-piece tier out of reach,
    # plus an effect-only tier that carries no numbers.
    set_raw = {
        "setbonus_list": [
            {"requireditems": 2, "sta": 120, "blockchance": 10.0},
            {"requireditems": 3, "effect": "Applies Enhance: Void Bane."},
            {"requireditems": 5, "basemodifier": 5.0},
        ]
    }
    for item_id, name in ((10, "Set Piece One"), (11, "Set Piece Two"), (12, "Set Piece Three")):
        add_item(item_id, name, setbonus_name="Vault Raiment", raw=set_raw)

    conn.commit()
    conn.close()
    monkeypatch.setattr(stat_deltas._items, "path", db_path)
    return db_path


# ---------------------------------------------------------------------------
# stat_totals
# ---------------------------------------------------------------------------


def test_totals_sum_items_and_adorns(items_db):
    totals = stat_totals([_slot("1", adorn_ids=("4",))])
    assert totals == {"potency": 10.0, "sta_eff": 30.0, "crit_bonus": 2.5}


def test_primary_attributes_fan_out_to_all_five(items_db):
    totals = stat_totals([_slot("3")])
    assert totals == {f"{a}_eff": 25.0 for a in ("str", "sta", "agi", "wis", "int")}


def test_duplicate_ids_are_weighted(items_db):
    totals = stat_totals([_slot("3"), _slot("3")])  # two copies of the same ring
    assert totals["sta_eff"] == 50.0


def test_unmapped_stats_are_ignored(items_db):
    assert stat_totals([_slot("5")]) == {}


def test_non_numeric_and_missing_ids_are_skipped(items_db):
    assert stat_totals([_slot(None), _slot("not-a-number"), _slot("99999")]) == {}


def test_missing_db_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(stat_deltas._items, "path", tmp_path / "absent.db")
    assert stat_totals([_slot("1")]) == {}


# ---------------------------------------------------------------------------
# Set bonuses
# ---------------------------------------------------------------------------


def test_set_bonus_tier_activates_at_required_count(items_db):
    totals = stat_totals([_slot("10"), _slot("11")])  # 2 of 3 pieces
    # 2-piece tier: +120 sta + 10 block chance. Effect-only and 5-piece tiers: nothing.
    assert totals == {"sta_eff": 120.0, "block_chance": 10.0}


def test_set_bonus_below_threshold_is_inactive(items_db):
    assert stat_totals([_slot("10")]) == {}


# ---------------------------------------------------------------------------
# compute_stat_deltas
# ---------------------------------------------------------------------------


def test_deltas_are_set_minus_worn_with_zeroes_dropped(items_db):
    deltas = compute_stat_deltas(
        set_equipment=[_slot("2")],  # +16 potency
        current_equipment=[_slot("1")],  # +10 potency +30 sta
    )
    assert deltas == {"potency": 6.0, "sta_eff": -30.0}


def test_identical_configs_produce_no_deltas(items_db):
    assert compute_stat_deltas([_slot("1")], [_slot("1")]) == {}


def test_set_bonus_gain_shows_in_delta(items_db):
    deltas = compute_stat_deltas(
        set_equipment=[_slot("10"), _slot("11")],
        current_equipment=[_slot("1")],
    )
    assert deltas == {"sta_eff": 90.0, "block_chance": 10.0, "potency": -10.0}
