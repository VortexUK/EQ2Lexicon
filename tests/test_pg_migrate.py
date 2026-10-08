"""pg_migrate: the family-header convention meta-test + runner behaviour.

The two-line header convention is load-bearing: the test scratch-schema
leaser retargets migration files by substituting exactly those lines, so
a file that drifts from the convention silently breaks per-test schema
isolation. The runner tests exercise the real ledger against the test
database (conftest provisions it).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend import pg, pg_migrate


def test_every_family_migration_follows_the_two_line_header() -> None:
    for path in pg_migrate.migration_files():
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert lines, f"{path.name} is empty"
        if lines[0].strip() == "-- global":
            with pytest.raises(ValueError, match="global"):
                pg_migrate.retarget(text, "scratch_x")
            continue
        # retarget() itself enforces the convention — it must not raise,
        # and the rewritten header must land on the requested schema.
        rewritten = pg_migrate.retarget(text, "scratch_x")
        head = rewritten.splitlines()[:2]
        assert head[0] == "create schema if not exists scratch_x;"
        assert head[1] == "set search_path to scratch_x, public;"


def test_retarget_rejects_nonconforming_files() -> None:
    with pytest.raises(ValueError, match="two-line family header"):
        pg_migrate.retarget("CREATE TABLE x (id int);", "scratch_x")


def test_runner_is_idempotent_and_records_the_ledger(tmp_path: Path) -> None:
    """A scratch migration directory applied twice: first run applies and
    records, second run is a no-op. Uses its own schema + ledger rows so
    the session schemas are untouched."""
    (tmp_path / "9001_scratch_family.sql").write_text(
        "create schema if not exists pgmig_scratch;\n"
        "set search_path to pgmig_scratch, public;\n"
        "CREATE TABLE widgets (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name text NOT NULL);\n",
        encoding="utf-8",
    )
    try:
        first = pg_migrate.run(directory=tmp_path)
        assert first == ["9001_scratch_family.sql"]
        second = pg_migrate.run(directory=tmp_path)
        assert second == []
        with pg.connection() as conn:
            row = conn.execute(
                "SELECT 1 AS ok FROM public.schema_migrations WHERE filename = %s",
                ("9001_scratch_family.sql",),
            ).fetchone()
            assert row is not None
            conn.execute("INSERT INTO pgmig_scratch.widgets (name) VALUES ('w')")
            got = conn.execute("SELECT id, name FROM pgmig_scratch.widgets").fetchone()
            assert got == {"id": 1, "name": "w"}
            conn.commit()
    finally:
        with pg.connection() as conn:
            conn.execute("DROP SCHEMA IF EXISTS pgmig_scratch CASCADE")
            conn.execute(
                "DELETE FROM public.schema_migrations WHERE filename = %s",
                ("9001_scratch_family.sql",),
            )
            conn.commit()


def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path: Path) -> None:
    (tmp_path / "9002_bad_family.sql").write_text(
        "create schema if not exists pgmig_bad;\n"
        "set search_path to pgmig_bad, public;\n"
        "CREATE TABLE ok_table (id int);\n"
        "CREATE TABLE broken (id int REFERENCES does_not_exist(id));\n",
        encoding="utf-8",
    )
    try:
        with pytest.raises(Exception):  # noqa: B017 — any psycopg error is fine
            pg_migrate.run(directory=tmp_path)
        with pg.connection() as conn:
            row = conn.execute(
                "SELECT 1 AS ok FROM public.schema_migrations WHERE filename = %s",
                ("9002_bad_family.sql",),
            ).fetchone()
            assert row is None, "failed migration must not be recorded"
            # the whole file rolled back — ok_table must not exist either
            reg = conn.execute("SELECT to_regclass('pgmig_bad.ok_table') AS r").fetchone()
            assert reg is not None and reg["r"] is None
    finally:
        with pg.connection() as conn:
            conn.execute("DROP SCHEMA IF EXISTS pgmig_bad CASCADE")
            conn.commit()


def test_runner_waits_out_a_conflicting_lock_then_gives_up(tmp_path: Path, monkeypatch) -> None:
    """Deploys overlap: the outgoing container's long read transaction holds
    the table a migration wants to ALTER. The runner must wait in short
    lock_timeout slices (so it never blocks other readers for long) and
    retry until the holder lets go — and fail cleanly if it never does."""
    import threading
    import time

    import psycopg

    (tmp_path / "9003_lock_family.sql").write_text(
        "create schema if not exists pgmig_lock;\n"
        "set search_path to pgmig_lock, public;\n"
        "ALTER TABLE held ADD COLUMN IF NOT EXISTS extra int;\n",
        encoding="utf-8",
    )
    with pg.connection() as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS pgmig_lock")
        conn.execute("CREATE TABLE IF NOT EXISTS pgmig_lock.held (id int)")
        conn.commit()
    monkeypatch.setattr(pg_migrate, "LOCK_TIMEOUT", "1s")
    monkeypatch.setattr(pg_migrate, "_LOCK_RETRY_SLEEP_S", 0.3)
    holder = psycopg.connect(pg.dsn())
    try:
        holder.execute("SELECT * FROM pgmig_lock.held")  # open transaction → ACCESS SHARE held

        def _release() -> None:
            time.sleep(3.0)
            holder.rollback()

        t = threading.Thread(target=_release)
        t.start()
        t0 = time.monotonic()
        applied = pg_migrate.run(directory=tmp_path)
        waited = time.monotonic() - t0
        t.join()
        assert applied == ["9003_lock_family.sql"]
        assert waited >= 2.5, "the runner should have had to wait for the holder"

        # A holder that never releases: the runner gives up at the deadline
        # with a clean rollback and no ledger row.
        (tmp_path / "9004_lock_family.sql").write_text(
            "create schema if not exists pgmig_lock;\n"
            "set search_path to pgmig_lock, public;\n"
            "ALTER TABLE held ADD COLUMN IF NOT EXISTS more int;\n",
            encoding="utf-8",
        )
        holder.execute("SELECT * FROM pgmig_lock.held")
        with pg.connection() as conn, pytest.raises(psycopg.errors.LockNotAvailable):
            pg_migrate.apply_migrations(conn, directory=tmp_path, lock_retry_deadline_s=2.0)
        holder.rollback()
        with pg.connection() as conn:
            row = conn.execute(
                "SELECT 1 AS ok FROM public.schema_migrations WHERE filename = %s", ("9004_lock_family.sql",)
            ).fetchone()
            assert row is None
    finally:
        holder.close()
        with pg.connection() as conn:
            conn.execute("DROP SCHEMA IF EXISTS pgmig_lock CASCADE")
            conn.execute(
                "DELETE FROM public.schema_migrations WHERE filename IN ('9003_lock_family.sql', '9004_lock_family.sql')"
            )
            conn.commit()
