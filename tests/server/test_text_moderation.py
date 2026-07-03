"""Unit tests for backend/server/core/text_moderation.py — the shared word
blocklist screen + free-text sanitiser."""

from __future__ import annotations

from backend.server.core.text_moderation import contains_blocked_term, sanitize_text


def test_clean_text_passes():
    assert contains_blocked_term("Team Alpha") is None
    assert contains_blocked_term("Progression") is None
    assert contains_blocked_term("") is None
    assert contains_blocked_term(None) is None


def test_plain_and_cased_hits():
    assert contains_blocked_term("porn stars") == "porn"
    assert contains_blocked_term("PORN") == "porn"
    assert contains_blocked_term("pornstreamer") == "porn"  # substring within a token


def test_catches_leetspeak_evasion():
    assert contains_blocked_term("p0rn") == "porn"
    assert contains_blocked_term("f4gg0t") == "faggot"
    assert contains_blocked_term("n1gg3r") == "nigger"


def test_catches_spacing_and_punctuation_evasion():
    assert contains_blocked_term("f a g g o t") == "faggot"
    assert contains_blocked_term("n.i.g.g.e.r") == "nigger"
    assert contains_blocked_term("p-o-r-n") == "porn"


def test_catches_invisible_char_evasion():
    # zero-width space wedged between letters
    assert contains_blocked_term("po​rn") == "porn"


def test_sanitize_trims_collapses_and_caps():
    assert sanitize_text("  Team   Alpha  ", max_len=40) == "Team Alpha"
    assert sanitize_text("A" * 100, max_len=40) == "A" * 40
    assert sanitize_text(None, max_len=40) == ""
    assert sanitize_text("", max_len=40) == ""


def test_sanitize_strips_control_and_invisible_chars():
    assert sanitize_text("Team​Name", max_len=40) == "TeamName"
    assert sanitize_text("bad‮reversed", max_len=40) == "badreversed"  # bidi override
    assert sanitize_text("tab\ttab", max_len=40) == "tab tab"  # control -> whitespace collapse
