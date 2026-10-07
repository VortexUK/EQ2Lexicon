"""RETIRED — do not run (scratch test of the item search query).

The search SQL lives in ``backend/server/api/item.py`` and the route tests cover it.
"""

from __future__ import annotations

if __name__ == "__main__":
    raise SystemExit(
        "retired: SQLite-era scratch test — item search now runs against the Postgres "
        "items schema and is covered by the route tests."
    )
