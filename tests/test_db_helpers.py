"""Tests for backend.db_helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.db_helpers import _repo_root, like_escape, resolve_db_path


class TestResolveDbPath:
    def test_env_var_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_DB_TEST_PATH", "/tmp/override.db")
        assert resolve_db_path("FAKE_DB_TEST_PATH", "ignored", "x.db") == Path("/tmp/override.db")

    def test_default_path_uses_repo_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FAKE_DB_TEST_PATH", raising=False)
        result = resolve_db_path("FAKE_DB_TEST_PATH", "items", "items.db")
        assert result == _repo_root() / "data" / "items" / "items.db"
        assert result.name == "items.db"
        assert result.parent.name == "items"

    def test_single_subpath_segment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FAKE_DB_TEST_PATH", raising=False)
        result = resolve_db_path("FAKE_DB_TEST_PATH", "users.db")
        assert result == _repo_root() / "data" / "users.db"

    def test_empty_env_var_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Empty string is falsy → use default.
        monkeypatch.setenv("FAKE_DB_TEST_PATH", "")
        result = resolve_db_path("FAKE_DB_TEST_PATH", "x.db")
        assert result == _repo_root() / "data" / "x.db"


class TestRepoRoot:
    def test_contains_backend(self) -> None:
        assert (_repo_root() / "backend").is_dir(), "repo root must contain a `backend/` dir"

    def test_cached(self) -> None:
        # Same object identity on repeat calls — lru_cache wins.
        assert _repo_root() is _repo_root()


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
