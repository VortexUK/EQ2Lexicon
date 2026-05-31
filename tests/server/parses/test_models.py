"""Tests for parses.models — type coercion helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.server.parses.models import (
    _to_bool_tf,
    _to_float,
    _to_int,
    _to_perc,
    _to_str_or_none,
    _to_ts,
)


class TestToInt:
    def test_int_passthrough(self):
        assert _to_int(42) == 42

    def test_str_int(self):
        assert _to_int("42") == 42

    def test_str_float_truncates(self):
        assert _to_int("42.9") == 42

    def test_none_to_zero(self):
        assert _to_int(None) == 0

    def test_empty_str_to_zero(self):
        assert _to_int("") == 0

    def test_garbage_to_zero(self):
        assert _to_int("nope") == 0


class TestToFloat:
    def test_float_passthrough(self):
        assert _to_float(3.14) == 3.14

    def test_int_to_float(self):
        assert _to_float(7) == 7.0

    def test_str_float(self):
        assert _to_float("2.5") == 2.5

    def test_none_to_zero(self):
        assert _to_float(None) == 0.0

    def test_empty_str_to_zero(self):
        assert _to_float("") == 0.0


class TestToStrOrNone:
    def test_strips(self):
        assert _to_str_or_none("  hello  ") == "hello"

    def test_empty_to_none(self):
        assert _to_str_or_none("") is None

    def test_whitespace_to_none(self):
        assert _to_str_or_none("   ") is None

    def test_none_passthrough(self):
        assert _to_str_or_none(None) is None


class TestToPerc:
    """ACT writes percentages as VARCHARs: '100%', '93%', '0%', '--', or empty."""

    def test_full_percent(self):
        assert _to_perc("100%") == 100.0

    def test_partial_percent(self):
        assert _to_perc("93%") == 93.0

    def test_zero_percent(self):
        assert _to_perc("0%") == 0.0

    def test_double_dash_to_zero(self):
        # ACT emits '--' when the value is meaningless (e.g. zero-damage combatant's damageperc)
        assert _to_perc("--") == 0.0

    def test_empty_to_zero(self):
        assert _to_perc("") == 0.0

    def test_none_to_zero(self):
        assert _to_perc(None) == 0.0

    def test_no_suffix(self):
        # Defensive: if ACT ever emits a bare number, still parse
        assert _to_perc("42.5") == 42.5

    def test_garbage_to_zero(self):
        assert _to_perc("???") == 0.0


class TestToBoolTf:
    """ACT writes 'T'/'F' for combatant_table.ally."""

    def test_true(self):
        assert _to_bool_tf("T") is True

    def test_false(self):
        assert _to_bool_tf("F") is False

    def test_case_insensitive(self):
        assert _to_bool_tf("t") is True
        assert _to_bool_tf("f") is False

    def test_none(self):
        assert _to_bool_tf(None) is False

    def test_unexpected_value(self):
        assert _to_bool_tf("Y") is False


class TestToTs:
    def test_iso_t_separator(self):
        assert _to_ts("2026-05-24T12:34:56") == datetime(2026, 5, 24, 12, 34, 56)

    def test_space_separator(self):
        assert _to_ts("2026-05-24 12:34:56") == datetime(2026, 5, 24, 12, 34, 56)

    def test_fractional_seconds(self):
        assert _to_ts("2026-05-24 12:34:56.789") == datetime(2026, 5, 24, 12, 34, 56, 789000)

    def test_none_returns_none(self):
        assert _to_ts(None) is None

    def test_empty_returns_none(self):
        assert _to_ts("") is None

    def test_garbage_returns_none(self):
        assert _to_ts("not a timestamp") is None

    def test_datetime_passthrough(self):
        dt = datetime(2026, 5, 24, 12, 34, 56)
        assert _to_ts(dt) is dt

    def test_iso_utc_z_suffix_returns_aware(self):
        """Plugin v0.1.1+ sends explicit UTC. We must round-trip with tzinfo set
        so _to_unix uses the proper offset (rather than re-labelling naive as
        UTC and silently drifting by the local-vs-UTC offset)."""
        got = _to_ts("2026-05-24T12:34:56Z")
        assert got == datetime(2026, 5, 24, 12, 34, 56, tzinfo=UTC)
        assert got is not None
        assert got.tzinfo is not None

    def test_legacy_naive_stays_naive(self):
        """Plugin v0.1.0 uploads (no Z, no offset) must continue to parse as
        a naive datetime — _to_unix treats them as UTC for backwards compat."""
        got = _to_ts("2026-05-24 12:34:56")
        assert got is not None
        assert got.tzinfo is None
