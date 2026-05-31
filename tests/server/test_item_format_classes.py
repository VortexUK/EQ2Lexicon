"""Unit tests for _format_classes — the class-set collapse helper.

Pure function; no app fixture needed. Tests cover the single-class,
single-archetype, exact-subclass-group, greedy-decomposition, all-classes,
empty-input, and fallback-comma-join paths per the COV-004 proposed scenarios.
"""

from __future__ import annotations

from backend.census.constants import FIGHTERS, MAGES, PRIESTS, SCOUTS
from backend.server.api.item import _format_classes


class TestFormatClasses:
    def test_single_class_returns_class_name(self):
        assert _format_classes(["Templar"]) == "Templar"

    def test_all_priests_collapses_to_archetype(self):
        # Drive from the constant so the test stays honest if membership changes.
        assert _format_classes(sorted(PRIESTS)) == "All Priests"

    def test_subclass_group_collapses_to_subgroup_name(self):
        # Templar + Inquisitor → "All Clerics" (the Clerics subclass group)
        assert _format_classes(["Templar", "Inquisitor"]) == "All Clerics"

    def test_greedy_decomposition_archetype_plus_stray(self):
        # All Warriors + one extra class → "All Warriors / Templar"
        assert _format_classes(["Guardian", "Berserker", "Templar"]) == "All Warriors / Templar"

    def test_all_26_collapses_to_all_classes(self):
        all_classes = sorted(FIGHTERS | PRIESTS | SCOUTS | MAGES)
        assert _format_classes(all_classes) == "All Classes"

    def test_empty_list_returns_empty_string(self):
        assert _format_classes([]) == ""

    def test_two_classes_no_group_comma_joined(self):
        # Conjuror + Wizard don't share a named subgroup → alphabetical comma join
        assert _format_classes(["Conjuror", "Wizard"]) == "Conjuror, Wizard"
