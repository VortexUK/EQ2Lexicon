"""Tests for backend/server/core/validation.py — pinning the regex shapes."""

from __future__ import annotations

import pytest

from backend.server.core.validation import (
    CHARACTER_NAME_MAX_LENGTH,
    WORLD_NAME_MAX_LENGTH,
    WORLD_NAME_RE,
    printable_label,
    sanitize_world,
    validate_character_name,
    validate_guild_name,
)


@pytest.mark.parametrize("name", ["Vortex", "Sihtric", "Menludiir"])
def test_character_name_accepts_valid(name: str) -> None:
    assert validate_character_name(name) == name


@pytest.mark.parametrize("name", ["", " ", "X" * 16, "Vor:tex", "Vortex Smith", "Vortex1"])
def test_character_name_rejects_invalid(name: str) -> None:
    assert validate_character_name(name) is None


@pytest.mark.parametrize(
    "world",
    [
        "Varsoon",
        "Wuoshi",
        "Kaladim",
        "Test Server",
        "Antonia Bayle",  # space + multi-word
        "Lucan D'Lere",  # apostrophe
        "Maj'Dul",  # apostrophe + short
    ],
)
def test_world_accepts_valid(world: str) -> None:
    assert sanitize_world(world) == world


@pytest.mark.parametrize(
    "world",
    [
        "",
        " ",
        "X" * 32,
        "/etc/passwd",
        "1Varsoon",
        "varsoon:other",  # colon (cache-collision vector)
        "../etc/passwd",  # path traversal
        "varsoon?c=1",  # URL meta
        "9Varsoon",  # leading digit
        "Varsoon" + "x" * 30,  # over-length
        "Varsoon\nKaladim",  # embedded control char
        None,  # None is a valid input per the signature
    ],
)
def test_world_rejects_invalid(world: str | None) -> None:
    assert sanitize_world(world) is None


def test_world_strips_leading_trailing_whitespace() -> None:
    assert sanitize_world(" Varsoon ") == "Varsoon"


# The request models bound a server name / character name with the MAX_LENGTH
# constants while the validators use the patterns. These pin the two to each
# other, so the model can never again allow a length the pattern refuses.
# EQ2Parser mirrors the server-name pattern and length in its upload client:
# a change that moves these numbers needs the same change there.


def test_world_name_pattern_is_the_mirrored_one() -> None:
    assert WORLD_NAME_RE.pattern == r"^[A-Za-z][A-Za-z0-9 '_-]{0,30}$"
    assert WORLD_NAME_MAX_LENGTH == 31


def test_world_max_length_is_the_patterns_own_boundary() -> None:
    longest = "V" * WORLD_NAME_MAX_LENGTH
    assert len(longest) == 31
    assert sanitize_world(longest) == longest
    assert sanitize_world(longest + "x") is None


def test_character_max_length_is_the_patterns_own_boundary() -> None:
    longest = "A" * CHARACTER_NAME_MAX_LENGTH
    assert len(longest) == 15
    assert validate_character_name(longest) == longest
    assert validate_character_name(longest + "a") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Varsoon", "Varsoon"),
        ("  padded  ", "padded"),
        ("line\r\nbreak", "linebreak"),  # CR/LF: the log-injection characters
        ("nul\x00byte", "nulbyte"),  # Postgres text cannot hold NUL
        ("tab\there", "tabhere"),
        ("‮evil", "evil"),  # right-to-left override
        ("zero​width", "zerowidth"),
        ("<img src=x onerror=alert(1)>", "<img src=x onerror=alert(1)>"),  # printable: escaped by the page, not here
        ("\r\n\t", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_printable_label_strips_non_printable(raw: str | None, expected: str) -> None:
    assert printable_label(raw, 64) == expected


def test_printable_label_cuts_to_max_length() -> None:
    assert printable_label("x" * 100, 31) == "x" * 31
    # Cut after cleaning, so removed characters do not use up the budget,
    # and a cut that lands on a space does not leave it trailing.
    assert printable_label("\x00" * 50 + "abc", 31) == "abc"
    assert printable_label("a" * 30 + " b", 31) == "a" * 30


@pytest.mark.parametrize("name", ["Exordium", "The Spitting Cobras", "Knights-Templar"])
def test_guild_accepts_valid(name: str) -> None:
    assert validate_guild_name(name) == name


@pytest.mark.parametrize("name", ["", " ", "'BadStart", "X" * 65])
def test_guild_rejects_invalid(name: str) -> None:
    assert validate_guild_name(name) is None
