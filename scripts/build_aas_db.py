"""Load locally-downloaded AA tree JSONs + aa_limits.json into the Postgres
``aas`` schema (DATABASE_URL — see backend/pg.py for the DSN resolution chain).

    python scripts/build_aas_db.py                 # data/AAs/trees -> the aas schema
    python scripts/build_aas_db.py --schema my_scratch_schema

Mirrors scripts/build_zones_db.py: reads every data/AAs/trees/{id}.json
(LOCAL census downloads — gitignored; fetch them first with
scripts/download_aa_trees.py) plus the committed aa_limits.json, upserts into
aa_trees/aa_nodes/aa_limits (tree_type + max_points precomputed), and stamps
_meta provenance. Schema DDL (and the full data seed a fresh database starts
from) is owned by db/migrations/0012_aas.sql — this script refreshes the data
after a new tree download.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from backend.eq2db import aas as aas_module  # noqa: E402
from backend.eq2db.aas import AACatalogue  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trees-dir", type=Path, default=ROOT / "data" / "AAs" / "trees")
    parser.add_argument("--limits", type=Path, default=ROOT / "data" / "AAs" / "aa_limits.json")
    parser.add_argument(
        "--schema",
        default=aas_module.SCHEMA,
        help=f"Target Postgres schema (default: {aas_module.SCHEMA!r}; override for scratch loads).",
    )
    args = parser.parse_args()

    if not args.trees_dir.is_dir():
        print(f"trees dir not found: {args.trees_dir}", file=sys.stderr)
        return 1

    cat = AACatalogue(args.schema)
    # No DSN configured → backend.pg raises here; that IS the refusal to run.
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

        cat.set_meta(conn, "built_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        cat.set_meta(conn, "built_from", str(args.trees_dir))
        cat.set_meta(conn, "tree_count", str(trees))
        cat.set_meta(conn, "node_count", str(nodes))
        cat.set_meta(conn, "limits_count", str(limits_count))
    finally:
        conn.close()

    aas_module.catalogue.clear_caches()
    print(f"aas schema built: {trees} trees, {nodes} nodes, {limits_count} xpac limits -> schema {args.schema!r}")
    for s in skipped:
        print(f"  skipped {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
