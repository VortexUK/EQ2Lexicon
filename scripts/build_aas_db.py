"""Build data/AAs/aas.db from the committed tree JSONs.

    python scripts/build_aas_db.py                # data/AAs/trees → data/AAs/aas.db
    python scripts/build_aas_db.py --db /tmp/x.db

Mirrors scripts/build_zones_db.py: reads every data/AAs/trees/{id}.json,
upserts into aa_trees/aa_nodes (tree_type + max_points precomputed), and
stamps _meta provenance. aas.db is committed (like classes.db) — rerun this
after refreshing the tree JSONs via scripts/download_aa_trees.py, then commit
the updated db.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.eq2db import aas as aas_db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trees-dir", type=Path, default=ROOT / "data" / "AAs" / "trees")
    parser.add_argument("--db", type=Path, default=aas_db.DB_PATH)
    args = parser.parse_args()

    if not args.trees_dir.is_dir():
        print(f"trees dir not found: {args.trees_dir}", file=sys.stderr)
        return 1

    conn = aas_db.init_db(args.db)
    trees = 0
    nodes = 0
    skipped: list[str] = []
    try:
        for path in sorted(args.trees_dir.glob("*.json"), key=lambda p: int(p.stem)):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                skipped.append(f"{path.name}: {exc}")
                continue
            count = aas_db.upsert_tree(conn, int(path.stem), data)
            if count == 0 and not (data.get("alternateadvancement_list") or []):
                skipped.append(f"{path.name}: no alternateadvancement_list")
                continue
            trees += 1
            nodes += count

        aas_db.set_meta(conn, "built_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        aas_db.set_meta(conn, "built_from", str(args.trees_dir))
        aas_db.set_meta(conn, "tree_count", str(trees))
        aas_db.set_meta(conn, "node_count", str(nodes))
    finally:
        conn.close()

    aas_db.clear_caches()
    print(f"aas.db built: {trees} trees, {nodes} nodes -> {args.db}")
    for s in skipped:
        print(f"  skipped {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
