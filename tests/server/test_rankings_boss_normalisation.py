"""Apostrophe-variant normalisation for boss-name canonicalisation.

Regression test for the issue where a curator-entered roster with one
apostrophe variant silently failed to match parses shipped with another.
"""

from backend.server.api.rankings import _normalise_boss_key


def test_straight_apostrophe_passthrough() -> None:
    assert _normalise_boss_key("D'Lizta Cheroon") == "d'lizta cheroon"


def test_curly_right_quote_normalised() -> None:
    # U+2019 right single quote → straight apostrophe
    assert _normalise_boss_key("D’Lizta Cheroon") == "d'lizta cheroon"


def test_modifier_letter_apostrophe_normalised() -> None:
    # U+02BC modifier letter apostrophe → straight
    assert _normalise_boss_key("DʼLizta Cheroon") == "d'lizta cheroon"


def test_lowercase_applied() -> None:
    assert _normalise_boss_key("D'LIZTA CHEROON") == "d'lizta cheroon"


def test_idempotent() -> None:
    once = _normalise_boss_key("D'Lizta")
    twice = _normalise_boss_key(once)
    assert once == twice == "d'lizta"


def test_no_apostrophe() -> None:
    assert _normalise_boss_key("Vyemm") == "vyemm"


def test_modifier_letter_double_prime_normalised() -> None:
    assert _normalise_boss_key("Vʺtekla") == "v'tekla"


def test_nbsp_folded_to_space() -> None:
    # NBSP between words → ASCII space
    assert _normalise_boss_key("V'Tekla K'Zalk") == "v'tekla k'zalk"


def test_nfc_composed_form() -> None:
    # Decomposed e + acute → composed é (NFC); confirm a known-decomposed input collapses
    assert _normalise_boss_key("é") == "é"


def test_strip_trailing_whitespace() -> None:
    assert _normalise_boss_key("D'Lizta Cheroon  ") == "d'lizta cheroon"


def test_fullwidth_apostrophe() -> None:
    assert _normalise_boss_key("D＇Lizta") == "d'lizta"
