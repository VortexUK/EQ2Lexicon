"""Tests for census/_coerce.py — pins the None/0/empty-string semantics."""

from __future__ import annotations

import pytest

from backend.census._coerce import coerce_float, coerce_int, coerce_str, coerce_str_or_none


@pytest.mark.parametrize("v,expected", [("42", 42), (42, 42), ("0", 0), (0, 0)])
def test_coerce_int_valid(v: object, expected: int) -> None:
    assert coerce_int(v) == expected


@pytest.mark.parametrize("v", [None, "", "abc", [1], {}])
def test_coerce_int_invalid(v: object) -> None:
    assert coerce_int(v) is None


@pytest.mark.parametrize("v,expected", [("3.14", 3.14), (3.14, 3.14), ("0", 0.0)])
def test_coerce_float_valid(v: object, expected: float) -> None:
    assert coerce_float(v) == expected


@pytest.mark.parametrize("v", [None, "", "abc"])
def test_coerce_float_invalid(v: object) -> None:
    assert coerce_float(v) is None


def test_coerce_str_none_to_empty() -> None:
    assert coerce_str(None) == ""


def test_coerce_str_or_none_whitespace_to_none() -> None:
    assert coerce_str_or_none("   ") is None
    assert coerce_str_or_none("") is None
    assert coerce_str_or_none("foo") == "foo"
    assert coerce_str_or_none(" foo ") == "foo"
