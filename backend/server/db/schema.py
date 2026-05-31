"""DDL — current shape of every table.

The actual CREATE TABLE / CREATE INDEX text lives in schema.sql alongside.
This module just loads it and exposes a single ``SCHEMA`` string so the
two existing consumers (init_db and the _assertions test) keep working.

ONLY safe-to-rerun statements belong in schema.sql. Any column-dependent
statement (e.g. CREATE INDEX on a column added by ALTER) MUST live in
migrations.py AFTER the corresponding ADD COLUMN — putting it in schema.sql
would silently crash on existing DBs (see memory
[[test-migrations-against-old-db-shape]]).
"""

from __future__ import annotations

from backend.sql_loader import load_sql

SCHEMA: str = load_sql(__file__)["all"]
