"""Regression test for BE-006: LIKE-pattern escape on user-supplied names.

A name containing ``%`` or ``_`` must not match more rows than the literal
name. Pre-fix this silently broadened the match and forced a table scan
(~1M item rows worst case).
"""

from __future__ import annotations

from backend.eq2db.items import _like_escape as items_like_escape
from backend.eq2db.recipes import _like_escape as recipes_like_escape
from backend.eq2db.spells import _like_escape as spells_like_escape

# Each module duplicates the helper for the Phase 1 surgical fix; Phase 2a
# consolidates them into web/lib/db_helpers.py. Until then, run the same
# contract against each to catch drift.
ESCAPERS = [
    ("items", items_like_escape),
    ("spells", spells_like_escape),
    ("recipes", recipes_like_escape),
]


def test_like_escape_percent() -> None:
    for name, esc in ESCAPERS:
        assert esc("foo%bar") == "foo\\%bar", name


def test_like_escape_underscore() -> None:
    for name, esc in ESCAPERS:
        assert esc("foo_bar") == "foo\\_bar", name


def test_like_escape_backslash() -> None:
    # Backslash escaped FIRST so the subsequent %/_ escapes don't double-escape.
    for name, esc in ESCAPERS:
        assert esc("foo\\bar") == "foo\\\\bar", name


def test_like_escape_plain() -> None:
    for name, esc in ESCAPERS:
        assert esc("foobar") == "foobar", name


def test_like_escape_all_three_metachars() -> None:
    for name, esc in ESCAPERS:
        assert esc("a\\b%c_d") == "a\\\\b\\%c\\_d", name
