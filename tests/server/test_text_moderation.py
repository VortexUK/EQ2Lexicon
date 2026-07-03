"""Unit tests for backend/server/core/text_moderation.py — the profanity screen
(backed by better-profanity) + free-text sanitiser.

We inject a neutral sentinel word into the profanity set rather than committing
real slurs to the repo; the sentinel is removed again after each test.
"""

from __future__ import annotations

import pytest
from better_profanity import profanity

from backend.server.core.text_moderation import contains_blocked_term, sanitize_text

_SENTINEL = "zzsentinelzz"


@pytest.fixture(autouse=True)
def _sentinel_word():
    profanity.add_censor_words([_SENTINEL])
    yield
    profanity.load_censor_words()  # reset to the default wordlist


def test_clean_text_passes():
    assert contains_blocked_term("Team Alpha") is None
    assert contains_blocked_term("Progression") is None
    assert contains_blocked_term("Grapevine Raiders") is None  # no substring false positive
    assert contains_blocked_term("") is None
    assert contains_blocked_term(None) is None


def test_flags_the_offending_word_for_the_audit_log():
    assert contains_blocked_term(_SENTINEL) == _SENTINEL
    assert contains_blocked_term(f"My {_SENTINEL.upper()} Team") == _SENTINEL  # case-insensitive


def test_invisible_chars_do_not_defeat_the_match():
    # a zero-width space wedged into the sentinel word is stripped before matching
    assert contains_blocked_term(_SENTINEL[:4] + "​" + _SENTINEL[4:]) == _SENTINEL


def test_sanitize_trims_collapses_and_caps():
    assert sanitize_text("  Team   Alpha  ", max_len=40) == "Team Alpha"
    assert sanitize_text("A" * 100, max_len=40) == "A" * 40
    assert sanitize_text(None, max_len=40) == ""
    assert sanitize_text("", max_len=40) == ""


def test_sanitize_strips_control_and_invisible_chars():
    assert sanitize_text("Team​Name", max_len=40) == "TeamName"
    assert sanitize_text("bad‮reversed", max_len=40) == "badreversed"  # bidi override
    assert sanitize_text("tab\ttab", max_len=40) == "tab tab"  # control -> whitespace collapse
