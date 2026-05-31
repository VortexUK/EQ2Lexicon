from __future__ import annotations

from backend.server.parses.boss import is_boss


class TestIsBoss:
    def test_named_boss_is_boss(self):
        assert is_boss("Tarinax") is True
        assert is_boss("The Shadowed One") is True

    def test_trash_article_names_are_not(self):
        assert is_boss("a krait patriarch") is False
        assert is_boss("an ancient guard") is False

    def test_empty_or_none_is_not(self):
        assert is_boss("") is False
        assert is_boss(None) is False  # type: ignore[arg-type]
