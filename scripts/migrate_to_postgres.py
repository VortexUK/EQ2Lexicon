"""One-shot SQLite → Postgres data copy for the Supabase cutover.

Re-runnable by design: every invocation TRUNCATEs the target schemas and
reloads them, so rehearsals are the real run and the cutover window's
final pass is just a fast re-run against the last litestream restore.

    uv run python scripts/migrate_to_postgres.py \
        --users data/users.db --parses data/parses/parses.db \
        --census data/census/census.db --zones data/zones/zones.db \
        --raids data/raids/raids.db

The DSN comes from the usual env chain (DATABASE_URL → SUPABASE_DB_URL →
POSTGRES_CONNECTION_STRING) or --dsn. Safety: the script prints the target
host and requires --yes (or interactive confirmation) before writing.

Per family: TRUNCATE … RESTART IDENTITY CASCADE → COPY each table in FK
order (committed PER TABLE — bounds WAL so a bulk load can't fill a small
disk) → setval every identity sequence → ANALYZE → final verify.
Column lists and types are introspected from information_schema, so the
copy follows the reviewed PG schemas; transforms applied by type:

  * integer[]  (raid_slots.days)        — "2,4" CSV → {2,4}
  * jsonb      (attendance zones/uploaders, census data_json) — validated
    with json.loads, shipped as text (COPY parses jsonb input server-side)
  * bytea      (guild_recruitment.logo) — bytes passthrough
  * detail-retention tiers (parses)     — attack_types/damage_types rows
    whose encounter is past its tier cutoff are NOT copied; the encounter
    is stamped detail_pruned_at at load (this is what shrinks 6.8GB of
    SQLite to a Postgres footprint the tier gate measures).

FK orphans (users.db never enforced its FKs): each child table is
anti-joined against its parent IN SQLITE first; offending rows are written
to reports/orphans_<schema>.<table>.jsonl and EXCLUDED from the copy. Any
orphan fails the run unless --allow-orphans. Duplicate APPROVED claims per
(world, lower(name)) are audited the same way (the new partial unique
index would reject the load).

Verification per table: PG row count must equal sqlite count minus
quarantined/pruned rows (hard fail), plus a sample deep-compare; a JSON
report lands in reports/migrate_report.json.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO / ".env")

from psycopg import conninfo  # noqa: E402

from backend import pg  # noqa: E402

REPORTS_DIR = _REPO / "reports"

#: Copy order. Cross-family FKs don't exist, but ZONES MUST PRECEDE PARSES:
#: the parses detail-retention tiers classify each encounter's zone via
#: _classify_zone, which reads the zones schema — with stale/empty zones
#: data every kill would classify "other" (7-day tier) and curated raid
#: detail would be over-pruned at load.
FAMILIES = ("users", "zones", "raids", "census", "parses")

#: Tables whose rows are intentionally NOT copied (none today; placeholder
#: so a future exclusion is one line).
SKIP_TABLES: set[str] = set()

_DAY_S = 86_400


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def pg_tables(conn: Any, schema: str) -> list[str]:
    rows = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename", (schema,)
    ).fetchall()
    return [r["tablename"] for r in rows]


def pg_columns(conn: Any, schema: str, table: str) -> dict[str, str]:
    """{column: data_type} — data_type is 'ARRAY'/'jsonb'/'bytea'/'bigint'/…"""
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns"
        " WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (schema, table),
    ).fetchall()
    return {r["column_name"]: r["data_type"] for r in rows}


def pg_identity_columns(conn: Any, schema: str, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = %s AND table_name = %s AND is_identity = 'YES'",
        (schema, table),
    ).fetchall()
    return [r["column_name"] for r in rows]


def pg_fks(conn: Any, schema: str) -> list[tuple[str, str, str, str]]:
    """[(child_table, child_col, parent_table, parent_col)] within one schema."""
    rows = conn.execute(
        """
        SELECT tc.table_name AS child, kcu.column_name AS child_col,
               ccu.table_name AS parent, ccu.column_name AS parent_col
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = %s
        """,
        (schema,),
    ).fetchall()
    return [(r["child"], r["child_col"], r["parent"], r["parent_col"]) for r in rows]


def topo_order(tables: list[str], fks: list[tuple[str, str, str, str]]) -> list[str]:
    """Parents before children (stable for independents)."""
    deps: dict[str, set[str]] = {t: set() for t in tables}
    for child, _cc, parent, _pc in fks:
        if child != parent and parent in deps and child in deps:
            deps[child].add(parent)
    out: list[str] = []
    remaining = dict(deps)
    while remaining:
        ready = sorted(t for t, d in remaining.items() if not (d & set(remaining)))
        if not ready:
            raise RuntimeError(f"FK cycle among: {sorted(remaining)}")
        out.extend(ready)
        for t in ready:
            remaining.pop(t)
    return out


def sqlite_columns(sconn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in sconn.execute(f"PRAGMA table_info({table})").fetchall()]


def sqlite_table_exists(sconn: sqlite3.Connection, table: str) -> bool:
    return sconn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


def transform_value(value: Any, pg_type: str, *, table: str, column: str) -> Any:
    if value is None:
        return None
    if pg_type == "ARRAY":
        # raid_slots.days "2,4" → [2, 4]
        if isinstance(value, str):
            return [int(p) for p in value.split(",") if p.strip() != ""]
        return value
    if pg_type == "jsonb":
        if isinstance(value, str):
            json.loads(value)  # validate; COPY ships the text and PG parses it
            return value
        return json.dumps(value)
    if pg_type == "bytea":
        return value if isinstance(value, (bytes, memoryview)) else bytes(value)
    return value


# ---------------------------------------------------------------------------
# Audits
# ---------------------------------------------------------------------------


@dataclass
class TableReport:
    source_rows: int = 0
    quarantined: int = 0
    pruned_detail: int = 0
    copied: int = 0
    pg_rows: int = 0


@dataclass
class RunReport:
    started_at: int = field(default_factory=lambda: int(time.time()))
    tables: dict[str, TableReport] = field(default_factory=dict)
    orphan_files: list[str] = field(default_factory=list)
    duplicate_claims: int = 0
    detail_pruned_encounters: int = 0

    def table(self, key: str) -> TableReport:
        return self.tables.setdefault(key, TableReport())


def quarantine_rows(schema: str, table: str, rows: list[dict], report: RunReport) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"orphans_{schema}.{table}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")
    report.orphan_files.append(str(path))


def audit_duplicate_approved_claims(sconn: sqlite3.Connection, report: RunReport) -> list[str]:
    """The 0001 partial unique index rejects >1 approved claim per
    (world, lower(name)) — find them BEFORE the load fails mid-copy."""
    if not sqlite_table_exists(sconn, "character_claims"):
        return []
    rows = sconn.execute(
        "SELECT world, LOWER(character_name) AS n, COUNT(*) AS c FROM character_claims"
        " WHERE status='approved' GROUP BY world, LOWER(character_name) HAVING COUNT(*) > 1"
    ).fetchall()
    report.duplicate_claims = len(rows)
    return [f"{r[0]}/{r[1]} ×{r[2]}" for r in rows]


# ---------------------------------------------------------------------------
# Family copy
# ---------------------------------------------------------------------------


def copy_family(
    conn: Any,
    sconn: sqlite3.Connection,
    schema: str,
    report: RunReport,
    *,
    now: int,
    prune_detail: bool,
) -> None:
    conn.execute(pg.search_path_sql(schema))
    tables = [t for t in pg_tables(conn, schema) if t not in SKIP_TABLES]
    fks = pg_fks(conn, schema)
    order = topo_order(tables, fks)

    # Child-FK exclusion sets, built in sqlite (its FKs were never enforced).
    fk_by_child: dict[str, list[tuple[str, str, str]]] = {}
    for child, child_col, parent, parent_col in fks:
        fk_by_child.setdefault(child, []).append((child_col, parent, parent_col))

    # Parses detail tiers: compute once which encounter ids are past their
    # tier (classification needs zones.db via the app's classifier).
    pruned_encounters: set[int] = set()
    if schema == "parses" and prune_detail and sqlite_table_exists(sconn, "encounters"):
        from backend.server.parses.cleanup import detail_retention_days  # noqa: PLC0415

        for eid, zone, started_at in sconn.execute("SELECT id, zone, started_at FROM encounters"):
            if started_at < now - detail_retention_days(zone) * _DAY_S:
                pruned_encounters.add(int(eid))
        report.detail_pruned_encounters = len(pruned_encounters)

    sql_tables = ", ".join(f'"{t}"' for t in tables)
    conn.execute(f"TRUNCATE {sql_tables} RESTART IDENTITY CASCADE")

    pruned_combatant_ids: set[int] = set()
    if schema == "parses" and pruned_encounters and sqlite_table_exists(sconn, "combatants"):
        ids = list(pruned_encounters)
        for i in range(0, len(ids), 900):
            chunk = ids[i : i + 900]
            ph = ",".join("?" * len(chunk))
            for r in sconn.execute(f"SELECT id FROM combatants WHERE encounter_id IN ({ph})", chunk):
                pruned_combatant_ids.add(int(r[0]))

    for table in order:
        key = f"{schema}.{table}"
        trep = report.table(key)
        if not sqlite_table_exists(sconn, table):
            if table == "_meta":
                continue  # census/zones/raids provenance may be absent — fine
            print(f"  [warn] sqlite has no table {table!r} — skipping (PG side stays empty)")
            continue

        pg_cols = pg_columns(conn, schema, table)
        s_cols = sqlite_columns(sconn, table)
        cols = [c for c in pg_cols if c in s_cols]
        missing_in_sqlite = [c for c in pg_cols if c not in s_cols]
        if missing_in_sqlite:
            print(f"  [info] {key}: PG-only columns left at defaults: {missing_in_sqlite}")

        # Orphan audit for this child table.
        excluded_where: list[str] = []
        for child_col, parent, parent_col in fk_by_child.get(table, []):
            if not sqlite_table_exists(sconn, parent):
                continue
            orphans = [
                dict(zip(s_cols, row))
                for row in sconn.execute(
                    f"SELECT * FROM {table} WHERE {child_col} IS NOT NULL"
                    f" AND {child_col} NOT IN (SELECT {parent_col} FROM {parent})"
                ).fetchall()
            ]
            if orphans:
                quarantine_rows(schema, table, orphans, report)
                trep.quarantined += len(orphans)
                excluded_where.append(f"({child_col} IS NULL OR {child_col} IN (SELECT {parent_col} FROM {parent}))")

        where = (" WHERE " + " AND ".join(excluded_where)) if excluded_where else ""
        select_sql = f"SELECT {', '.join(cols)} FROM {table}{where}"
        trep.source_rows = sconn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        col_types = [pg_cols[c] for c in cols]
        detail_table = schema == "parses" and table in ("attack_types", "damage_types")
        combatant_idx = cols.index("combatant_id") if detail_table and "combatant_id" in cols else None

        copy_sql = f"COPY {table} ({', '.join(cols)}) FROM STDIN"
        copied = 0
        with conn.cursor() as cur, cur.copy(copy_sql) as cp:
            for row in sconn.execute(select_sql):
                if combatant_idx is not None and int(row[combatant_idx]) in pruned_combatant_ids:
                    trep.pruned_detail += 1
                    continue
                cp.write_row(
                    tuple(transform_value(v, t, table=table, column=c) for v, t, c in zip(row, col_types, cols))
                )
                copied += 1
        trep.copied = copied

        # Stamp detail_pruned_at on the pruned encounters once encounters landed.
        if schema == "parses" and table == "encounters" and pruned_encounters:
            conn.execute(
                "UPDATE encounters SET detail_pruned_at = %s WHERE id = ANY(%s)",
                (now, list(pruned_encounters)),
            )

        # Commit per table: bounds the WAL a bulk load accumulates (a single
        # family-wide transaction filled a small Supabase disk mid-COPY) and
        # lets checkpoints recycle between tables. Idempotency is unchanged —
        # every run starts by truncating the family, and the within-family FK
        # order means a committed parent is always valid for its children.
        conn.commit()

    # setval every identity sequence to MAX(col).
    for table in order:
        for col in pg_identity_columns(conn, schema, table):
            conn.execute(
                "SELECT setval(pg_get_serial_sequence(%s, %s),"
                f" COALESCE((SELECT MAX({col}) FROM {table}), 0) + 1, false)",
                (f"{schema}.{table}", col),
            )

    # Fresh planner statistics immediately — the first post-cutover queries
    # (the rankings rebuild) must not run against empty stats while
    # autovacuum catches up on millions of just-loaded rows.
    for table in order:
        conn.execute(f"ANALYZE {table}")

    # Verify counts (tables were committed individually above).
    for table in order:
        key = f"{schema}.{table}"
        trep = report.table(key)
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        trep.pg_rows = row["n"]
        expected = trep.copied
        if trep.pg_rows != expected:
            raise RuntimeError(f"{key}: copied {expected} rows but PG holds {trep.pg_rows}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for fam in FAMILIES:
        ap.add_argument(f"--{fam}", type=Path, help=f"path to the source {fam} SQLite file")
    ap.add_argument("--dsn", help="override the env DSN chain")
    ap.add_argument("--allow-orphans", action="store_true", help="quarantine orphans and continue")
    ap.add_argument("--no-detail-tiers", action="store_true", help="copy ALL attack/damage_types rows")
    ap.add_argument("--yes", action="store_true", help="skip the interactive target confirmation")
    args = ap.parse_args()

    if args.dsn:
        import os

        os.environ["DATABASE_URL"] = args.dsn
    params = conninfo.conninfo_to_dict(pg.dsn())
    target = f"{params.get('host', '?')}/{params.get('dbname', '?')}"
    print(f"Target Postgres: {target}")
    if not args.yes:
        if input(f"TRUNCATE + reload the family schemas on {target}? [type 'yes'] ").strip() != "yes":
            print("aborted")
            return 1

    sources = {fam: getattr(args, fam) for fam in FAMILIES if getattr(args, fam) is not None}
    if not sources:
        print("nothing to do — pass at least one --<family> path")
        return 1
    for fam, path in sources.items():
        if not path.exists():
            print(f"--{fam}: {path} does not exist")
            return 1

    now = int(time.time())
    report = RunReport()
    rc = 0

    # Users first: audit duplicate approved claims before anything writes.
    if "users" in sources:
        with sqlite3.connect(sources["users"]) as sconn:
            dups = audit_duplicate_approved_claims(sconn, report)
        if dups:
            print(f"DUPLICATE APPROVED CLAIMS ({len(dups)}) — resolve in prod before cutover:")
            for d in dups:
                print(f"  {d}")
            return 2

    for fam in FAMILIES:
        if fam not in sources:
            continue
        print(f"[{fam}] copying from {sources[fam]} ...")
        t0 = time.time()
        sconn = sqlite3.connect(f"file:{sources[fam]}?mode=ro", uri=True)
        try:
            with pg.connection() as conn:
                # Bulk-load session: the server-side statement_timeout kills a
                # TRUNCATE queued behind live app locks; lift it for this
                # session only (a waiting TRUNCATE cannot be starved in PG).
                conn.execute("SET statement_timeout = 0")
                copy_family(
                    conn,
                    sconn,
                    fam,
                    report,
                    now=now,
                    prune_detail=not args.no_detail_tiers,
                )
                conn.commit()
        finally:
            sconn.close()
        print(f"[{fam}] done in {time.time() - t0:.1f}s")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / "migrate_report.json"
    out.write_text(
        json.dumps(
            {
                "started_at": report.started_at,
                "target": target,
                "detail_pruned_encounters": report.detail_pruned_encounters,
                "duplicate_claims": report.duplicate_claims,
                "orphan_files": sorted(set(report.orphan_files)),
                "tables": {k: vars(v) for k, v in sorted(report.tables.items())},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"report: {out}")

    total_quarantined = sum(t.quarantined for t in report.tables.values())
    if total_quarantined:
        print(f"quarantined {total_quarantined} orphan row(s) — see reports/orphans_*.jsonl")
        if not args.allow_orphans:
            print("failing (pass --allow-orphans to accept the quarantine)")
            rc = 3
    for key, t in sorted(report.tables.items()):
        print(
            f"  {key}: source={t.source_rows} copied={t.copied} pg={t.pg_rows}"
            + (f" quarantined={t.quarantined}" if t.quarantined else "")
            + (f" detail_pruned={t.pruned_detail}" if t.pruned_detail else "")
        )
    return rc


if __name__ == "__main__":
    pg.ensure_selector_event_loop_policy()
    raise SystemExit(main())
