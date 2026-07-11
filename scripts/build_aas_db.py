"""Build data/AAs/aas.db from locally-downloaded tree JSONs + aa_limits.json.

    python scripts/build_aas_db.py                # data/AAs/trees → data/AAs/aas.db
    python scripts/build_aas_db.py --db /tmp/x.db

Mirrors scripts/build_zones_db.py: reads every data/AAs/trees/{id}.json
(LOCAL census downloads — gitignored; fetch them first with
scripts/download_aa_trees.py) plus the committed aa_limits.json, upserts into
aa_trees/aa_nodes/aa_limits (tree_type + max_points precomputed), and stamps
_meta provenance. aas.db is committed (like classes.db) — commit the updated
db after a rebuild.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.eq2db import aas as aas_module  # noqa: E402
from backend.eq2db.aas import AACatalogue, set_meta  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trees-dir", type=Path, default=ROOT / "data" / "AAs" / "trees")
    parser.add_argument("--limits", type=Path, default=ROOT / "data" / "AAs" / "aa_limits.json")
    parser.add_argument("--db", type=Path, default=aas_module.DB_PATH)
    args = parser.parse_args()

    if not args.trees_dir.is_dir():
        print(f"trees dir not found: {args.trees_dir}", file=sys.stderr)
        return 1

    cat = AACatalogue(args.db)
    conn = cat.init_db()
    trees = 0
    nodes = 0
    skipped: list[str] = []
    try:
        # Only numeric stems are tree files — a stray index.json / editor backup
        # must not abort the whole rebuild.
        tree_files = sorted(
            (p for p in args.trees_dir.glob("*.json") if p.stem.isdigit()),
            key=lambda p: int(p.stem),
        )
        for path in args.trees_dir.glob("*.json"):
            if not path.stem.isdigit():
                skipped.append(f"{path.name}: non-numeric filename stem")
        for path in tree_files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                skipped.append(f"{path.name}: {exc}")
                continue
            try:
                count = cat.upsert_tree(conn, int(path.stem), data)
            except Exception as exc:  # e.g. IntegrityError on a corrupt census dupe
                skipped.append(f"{path.name}: {exc}")
                continue
            if count == 0 and not (data.get("alternateadvancement_list") or []):
                skipped.append(f"{path.name}: no alternateadvancement_list")
                continue
            trees += 1
            nodes += count

        limits_count = 0
        if args.limits.exists():
            limits = json.loads(args.limits.read_text(encoding="utf-8"))
            for xpac, entry in limits.items():
                cat.upsert_limits(conn, xpac, entry)
                limits_count += 1
        else:
            print(f"limits file not found (skipping): {args.limits}", file=sys.stderr)

        set_meta(conn, "built_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        set_meta(conn, "built_from", str(args.trees_dir))
        set_meta(conn, "tree_count", str(trees))
        set_meta(conn, "node_count", str(nodes))
        set_meta(conn, "limits_count", str(limits_count))
    finally:
        conn.close()

    aas_module.catalogue.clear_caches()
    print(f"aas.db built: {trees} trees, {nodes} nodes, {limits_count} xpac limits -> {args.db}")
    for s in skipped:
        print(f"  skipped {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
