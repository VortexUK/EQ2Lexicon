"""Tests for web.lib.log_safety — COV-022.

Target: ≥ 90 % on web.lib.log_safety.
"""

from __future__ import annotations

import pytest

from backend.core.log_safety import scrub


class TestScrub:
    def test_safe_string_passes_through(self):
        assert scrub("Varsoon") == "Varsoon"

    def test_strips_newline(self):
        assert scrub("foo\nbar") == "foo bar"

    def test_strips_carriage_return(self):
        assert scrub("foo\rbar") == "foo bar"

    def test_strips_both_cr_and_lf(self):
        assert scrub("foo\r\nbar") == "foo  bar"

    def test_stringifies_none(self):
        result = scrub(None)
        assert result == "None"

    def test_stringifies_integer(self):
        result = scrub(42)
        assert result == "42"

    def test_stringifies_object(self):
        class Obj:
            def __str__(self):
                return "custom"

        assert scrub(Obj()) == "custom"

    def test_empty_string(self):
        assert scrub("") == ""

    def test_multiline_injection_attempt(self):
        payload = "normal\nFAKE LOG ENTRY: admin logged in"
        result = scrub(payload)
        assert "\n" not in result
        assert "normal FAKE LOG ENTRY: admin logged in" == result

    def test_no_truncation_by_default(self):
        # scrub does not truncate — just strips CR/LF
        long_value = "x" * 1000
        assert scrub(long_value) == long_value
