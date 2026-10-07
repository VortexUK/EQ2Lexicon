"""Tests for backend.db_helpers.

(resolve_db_path / _repo_root were retired with the SQLite-era path
resolution — the remaining read-only mirrors take their env overrides
directly and the Postgres families have no file paths at all. like_escape
lives on: it guards the LIKE fallbacks in the Postgres catalogues too.)
"""

from __future__ import annotations

from backend.db_helpers import like_escape


class TestLikeEscape:
    def test_escapes_percent(self) -> None:
        assert like_escape("50%") == "50\\%"

    def test_escapes_underscore(self) -> None:
        assert like_escape("foo_bar") == "foo\\_bar"

    def test_escapes_backslash_first(self) -> None:
        # Backslash must be escaped first so subsequent escapes don't double-up.
        assert like_escape("\\%") == "\\\\\\%"

    def test_passthrough_letters_and_digits(self) -> None:
        assert like_escape("Lightning Palm III") == "Lightning Palm III"

    def test_empty_string(self) -> None:
        assert like_escape("") == ""

    def test_combination(self) -> None:
        # All three escapable chars + ordinary text.
        assert like_escape("100%_done\\") == "100\\%\\_done\\\\"
