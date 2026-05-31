"""Tiny SQL-resource loader — no dependencies.

SQL queries live in sibling ``.sql`` files alongside the Python modules that
use them. Each file holds one or more named query blocks delimited by
``-- :name <ident>`` markers (one identifier per line). Example::

    -- :name list_by_type
    SELECT z.id, z.name
    FROM zones z
    JOIN zone_types t ON t.zone_id = z.id
    WHERE t.type = ?;

    -- :name count_by_type
    SELECT COUNT(*) FROM zone_types WHERE type = ?;

Python side::

    from backend.sql_loader import load_sql
    SQL = load_sql(__file__)
    conn.execute(SQL["list_by_type"], (zone_type,))

Why a custom loader and not aiosql/yesql:
  - Zero new dependencies. The parser is ~25 lines of boring Python.
  - We don't need the auto-generated function bindings aiosql provides;
    the project uses bare ``conn.execute(SQL[...], params)`` everywhere.
  - F-string composition (``f\"... {SQL['fragment']} ...\"``) for dynamic
    identifiers/ORDER BY/LIMIT stays first-class — load the fragment and
    interpolate where needed.

Conventions:
  - One ``.sql`` file per Python module that has DML. Path mirrors the
    module: ``backend/eq2db/zones.py`` <-> ``backend/eq2db/zones.sql``.
  - DDL (CREATE TABLE/INDEX) stays embedded in the ``.py`` next to the
    ``init_db()`` migration code that runs it — keeping DDL and the
    migrations that depend on it co-located beats hauling it out.
  - Block names are valid Python identifiers ([a-z_][a-z0-9_]*). The
    loader raises on duplicates so a typo can't silently shadow.
"""

from __future__ import annotations

import re
from pathlib import Path

_NAME_RE = re.compile(r"^\s*--\s*:name\s+([a-z_][a-z0-9_]*)\s*$", re.IGNORECASE)


def parse_sql(text: str) -> dict[str, str]:
    """Parse a ``.sql`` file body into ``{name: sql}``. Trailing semicolons
    are kept; leading/trailing blank lines stripped. Raises ``ValueError`` on
    duplicate block names or text before the first ``-- :name`` marker
    (so a file with no markers is a clear error)."""
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        m = _NAME_RE.match(raw)
        if m:
            name = m.group(1)
            if name in blocks:
                raise ValueError(f"duplicate :name {name!r} at line {lineno}")
            blocks[name] = []
            current = name
            continue
        if current is None:
            # Allow blank lines and other comments at the top of the file.
            if raw.strip() and not raw.lstrip().startswith("--"):
                raise ValueError(f"line {lineno}: SQL text before first ':name' marker — every block must be named")
            continue
        blocks[current].append(raw)
    # Trim trailing blank lines and trailing pure-comment lines from each block.
    # Section-divider comments between blocks (e.g. `-- Zone CRUD --`) would
    # otherwise leak into the previous block's body and break interpolation
    # (a column-list fragment composed via str.format would end up with
    # comment text spliced into the middle of the SELECT).
    out: dict[str, str] = {}
    for name, lines in blocks.items():
        while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("--")):
            lines.pop()
        out[name] = "\n".join(lines).strip()
    return out


def load_sql(module_file: str) -> dict[str, str]:
    """Load the ``.sql`` sibling of ``module_file`` (the caller's ``__file__``).

    A Python module at ``backend/eq2db/zones.py`` finds its queries at
    ``backend/eq2db/zones.sql``. ``FileNotFoundError`` if the sibling is
    missing — keeping the failure mode explicit so a misnamed file shows up
    at import time, not at first call.
    """
    sql_path = Path(module_file).with_suffix(".sql")
    if not sql_path.is_file():
        raise FileNotFoundError(f"SQL resource file not found: {sql_path}")
    return parse_sql(sql_path.read_text(encoding="utf-8"))
