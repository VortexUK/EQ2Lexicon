"""Build data/classes/classes.db from the static CLASS_SEED.

Instant (no network). The DB is gitignored — rebuild locally and copy to the
Railway volume, same as recipes.db / spells.db.

Usage:
    uv run python scripts/build_classes_db.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from census import classes_db  # noqa: E402


def main() -> None:
    conn = classes_db.init_db(classes_db.DB_PATH)
    try:
        n = classes_db.seed(conn)
    finally:
        conn.close()
    print(f"seeded {n} classes -> {classes_db.DB_PATH}")
    roles = Counter(c.role for c in classes_db.CLASS_SEED)
    for role, count in roles.most_common():
        print(f"  {count:2d}  {role}")


if __name__ == "__main__":
    main()
