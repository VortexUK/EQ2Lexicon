"""Tests for web/lib/validation.py — pinning the regex shapes."""

from __future__ import annotations

import pytest

from backend.server.core.validation import (
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


@pytest.mark.parametrize("name", ["Exordium", "The Spitting Cobras", "Knights-Templar"])
def test_guild_accepts_valid(name: str) -> None:
    assert validate_guild_name(name) == name


@pytest.mark.parametrize("name", ["", " ", "'BadStart", "X" * 65])
def test_guild_rejects_invalid(name: str) -> None:
    assert validate_guild_name(name) is None
