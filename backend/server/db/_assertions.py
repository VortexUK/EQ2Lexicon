"""Schema-vs-migrations drift assertion. Run from init_db AFTER migrations,
so any column added to SCHEMA but missed in migrations.py raises loudly at
test startup rather than crashing at runtime on a pre-migration-shape DB.

Memory [[test-migrations-against-old-db-shape]]: this is the codified form
of the lesson learnt when a column-dependent index in SCHEMA crashed the
production startup against an existing DB.
"""

from __future__ import annotations

import re
import sqlite3

from backend.server.db.schema import SCHEMA


def _split_column_defs(columns_block: str) -> list[str]:
    """Split a CREATE TABLE column list on top-level commas only.

    DEFAULT (strftime('%s','now')) contains a comma inside parentheses; a
    naive str.split(",") would break that into two fragments and misidentify
    the second fragment as a column name.  This function counts paren depth
    and only splits when depth == 0.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in columns_block:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def assert_schema_complete(conn: sqlite3.Connection) -> None:
    """Raise AssertionError if any column in SCHEMA is missing from the live DB.

    Parses SCHEMA for every ``CREATE TABLE IF NOT EXISTS`` block, extracts
    the expected column names, then cross-checks each against
    ``PRAGMA table_info``. Any column that appears in SCHEMA but is absent
    from the live DB means a migration is missing.

    Only runs when assertions are enabled (the default for pytest / dev);
    in production with ``python -O`` the assertion body is compiled out and
    this is a no-op — so prod is unaffected by this check.
    """
    # Parse SCHEMA for every "CREATE TABLE IF NOT EXISTS x (col1 ..., col2 ...)" block.
    create_re = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+) \(([^;]+)\);", re.DOTALL)
    for table_name, columns_block in create_re.findall(SCHEMA):
        expected_cols = {
            line.strip().split()[0]
            for line in _split_column_defs(columns_block)
            if line.strip()
            and not line.strip().upper().startswith("PRIMARY")
            and not line.strip().upper().startswith("FOREIGN")
            and not line.strip().startswith("--")
            and not line.strip().startswith("UNIQUE")
        }
        actual_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}
        missing = expected_cols - actual_cols
        assert not missing, (
            f"Schema drift on table {table_name!r}: SCHEMA declares "
            f"columns {missing} that don't exist on the live DB. The migration "
            f"for these columns is missing from web/db/migrations.py."
        )
