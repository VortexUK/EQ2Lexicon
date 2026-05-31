"""Tests for web.lib.sql_helpers — COV-023.

Target: ≥ 90 % on web.lib.sql_helpers.
"""

from __future__ import annotations

import pytest

from backend.server.core.sql_helpers import build_where


class TestBuildWhere:
    def test_empty_list_returns_empty_string(self):
        assert build_where([]) == ""

    def test_single_clause_returns_where_prefix(self):
        assert build_where(["a=1"]) == "WHERE a=1"

    def test_two_clauses_joined_with_and(self):
        assert build_where(["a=1", "b=2"]) == "WHERE a=1 AND b=2"

    def test_three_clauses(self):
        result = build_where(["a=1", "b=2", "c=3"])
        assert result == "WHERE a=1 AND b=2 AND c=3"

    def test_can_be_embedded_in_sql(self):
        where = build_where(["status = ?"])
        sql = f"SELECT * FROM foo {where}"
        assert sql == "SELECT * FROM foo WHERE status = ?"

    def test_empty_list_embeds_cleanly(self):
        where = build_where([])
        sql = f"SELECT * FROM foo {where}"
        # Trailing space is fine — SQL is forgiving
        assert "WHERE" not in sql
