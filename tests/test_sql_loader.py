"""Tests for backend.sql_loader.

The loader is intentionally tiny. These cover the failure modes that would
otherwise turn into runtime mystery bugs (a typo'd ``-- :name``, a duplicate
identifier, SQL above the first marker)."""

from __future__ import annotations

import pytest

from backend.sql_loader import parse_sql


def test_single_block():
    text = """-- :name get_user
SELECT * FROM users WHERE id = ?;
"""
    assert parse_sql(text) == {"get_user": "SELECT * FROM users WHERE id = ?;"}


def test_multiple_blocks():
    text = """-- :name list_all
SELECT * FROM users;

-- :name count_all
SELECT COUNT(*) FROM users;
"""
    parsed = parse_sql(text)
    assert parsed["list_all"] == "SELECT * FROM users;"
    assert parsed["count_all"] == "SELECT COUNT(*) FROM users;"


def test_block_with_inline_comments_kept():
    text = """-- :name complex
SELECT *
-- this is a comment inside the query, kept verbatim
FROM users
WHERE id = ?;
"""
    parsed = parse_sql(text)
    assert "-- this is a comment inside the query" in parsed["complex"]


def test_section_divider_comments_between_blocks_not_appended():
    """Section dividers between blocks must NOT bleed into the previous
    block's body — otherwise a fragment composed via str.format ends up
    with comment text spliced into the middle of the SELECT."""
    text = """-- :name col_list_fragment
id, name, value

-- ---------------------------------------------------------------------------
-- Section divider between blocks
-- ---------------------------------------------------------------------------

-- :name find_by_id
SELECT {cols} FROM items WHERE id = ?;
"""
    parsed = parse_sql(text)
    assert parsed["col_list_fragment"] == "id, name, value"
    assert "Section divider" not in parsed["col_list_fragment"]


def test_inline_comments_inside_sql_still_preserved():
    """Trimming trailing comments must not strip legitimate inline
    comments that sit between SQL lines."""
    text = """-- :name complex
SELECT *
-- this comment is in the middle
FROM users
WHERE id = ?;
"""
    parsed = parse_sql(text)
    assert "-- this comment is in the middle" in parsed["complex"]
    assert parsed["complex"].endswith("WHERE id = ?;")


def test_blank_lines_around_blocks_stripped():
    text = """

-- :name foo

SELECT 1;

"""
    assert parse_sql(text) == {"foo": "SELECT 1;"}


def test_top_of_file_comments_allowed():
    """Header comments above the first :name marker shouldn't error."""
    text = """-- File: shared queries.
-- These all run against the parses DB.

-- :name foo
SELECT 1;
"""
    assert parse_sql(text) == {"foo": "SELECT 1;"}


def test_sql_before_first_name_raises():
    text = """SELECT 1;

-- :name foo
SELECT 2;
"""
    with pytest.raises(ValueError, match="before first ':name' marker"):
        parse_sql(text)


def test_duplicate_name_raises():
    text = """-- :name foo
SELECT 1;

-- :name foo
SELECT 2;
"""
    with pytest.raises(ValueError, match="duplicate :name 'foo'"):
        parse_sql(text)


def test_name_must_be_identifier():
    """The marker regex requires an identifier — '0bad' won't match the rule
    and so the line is treated as SQL (which then triggers the
    'before first :name' guard if it's the first content)."""
    text = """-- :name 0badname
SELECT 1;
"""
    with pytest.raises(ValueError, match="before first ':name' marker"):
        parse_sql(text)


def test_empty_file():
    assert parse_sql("") == {}


def test_loader_resolves_sibling():
    """End-to-end: backend/eq2db/_meta.py picks up backend/eq2db/_meta.sql."""
    from backend.eq2db._meta import _SQL

    assert "select_value" in _SQL
    assert "upsert" in _SQL
    assert "FROM _meta" in _SQL["select_value"]
