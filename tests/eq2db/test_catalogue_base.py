"""Tests for the SQLite base-class dunder surface (backend/db_catalogue.py:
PathBound / BaseCatalogue — still the base for the SQLite-resident
catalogues: aas, classes, spell_effects, …).

Exercised through AACatalogue (a real concrete subclass, with per-instance
caches) plus a minimal local ``_ToyCatalogue`` stand-in (one table, no
caches) so the behaviour is proven on the BASE machinery itself.
(Recipe/Spell catalogues were the original concrete examples but moved to
PgCatalogue in the Phase-2 Postgres cutover — same intent, SQLite-resident
subclasses now carry the cases.)
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from backend.db_catalogue import BaseCatalogue
from backend.eq2db.aas import AACatalogue


class _ToyCatalogue(BaseCatalogue):
    """Minimal concrete BaseCatalogue (one table, no caches) — the SQLite
    stand-in for the former RecipeCatalogue examples. Keeps the tests about
    the base-class machinery, not any one domain schema."""

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE IF NOT EXISTS toys (id INTEGER PRIMARY KEY, name TEXT)")


# ---------------------------------------------------------------------------
# __repr__ / _cache_info
# ---------------------------------------------------------------------------


class TestRepr:
    def test_missing_db(self, tmp_path: Path):
        cat = _ToyCatalogue(tmp_path / "toys.db")
        r = repr(cat)
        assert r.startswith("_ToyCatalogue(")
        assert "missing" in r
        # !r-escaped on Windows, so compare against the repr of the string
        assert repr(str(tmp_path / "toys.db")) in r

    def test_ready_db(self, tmp_path: Path):
        cat = _ToyCatalogue(tmp_path / "toys.db")
        cat.init_db().close()
        assert "ready" in repr(cat)

    def test_cache_sizes_rendered(self, tmp_path: Path):
        # AACatalogue is the surviving SQLite subclass with per-instance
        # caches (was SpellCatalogue's crc_cache before the PG cutover).
        cat = AACatalogue(tmp_path / "aas.db")
        assert "trees=0" in repr(cat)
        cat._trees[123] = None
        assert "trees=1" in repr(cat)

    def test_no_cache_subclass_has_no_cache_suffix(self, tmp_path: Path):
        r = repr(_ToyCatalogue(tmp_path / "toys.db"))
        assert r.endswith("missing)")


# ---------------------------------------------------------------------------
# __bool__
# ---------------------------------------------------------------------------


class TestBool:
    def test_false_when_db_missing(self, tmp_path: Path):
        assert not _ToyCatalogue(tmp_path / "nope.db")

    def test_true_when_db_exists(self, tmp_path: Path):
        cat = _ToyCatalogue(tmp_path / "toys.db")
        cat.init_db().close()
        assert cat


# ---------------------------------------------------------------------------
# __eq__ / __hash__
# ---------------------------------------------------------------------------


class TestEqHash:
    def test_same_class_same_path_equal(self, tmp_path: Path):
        p = tmp_path / "toys.db"
        assert _ToyCatalogue(p) == _ToyCatalogue(p)
        assert hash(_ToyCatalogue(p)) == hash(_ToyCatalogue(p))

    def test_different_path_not_equal(self, tmp_path: Path):
        assert _ToyCatalogue(tmp_path / "a.db") != _ToyCatalogue(tmp_path / "b.db")

    def test_different_class_same_path_not_equal(self, tmp_path: Path):
        p = tmp_path / "x.db"
        assert _ToyCatalogue(p) != AACatalogue(p)

    def test_non_catalogue_not_equal(self, tmp_path: Path):
        assert _ToyCatalogue(tmp_path / "x.db") != "x.db"

    def test_usable_as_dict_key(self, tmp_path: Path):
        p = tmp_path / "toys.db"
        d = {_ToyCatalogue(p): "hit"}
        assert d[_ToyCatalogue(p)] == "hit"


# ---------------------------------------------------------------------------
# __fspath__
# ---------------------------------------------------------------------------


class TestFspath:
    def test_os_fspath(self, tmp_path: Path):
        p = tmp_path / "toys.db"
        assert os.fspath(_ToyCatalogue(p)) == str(p)

    def test_path_conversion(self, tmp_path: Path):
        p = tmp_path / "toys.db"
        assert Path(_ToyCatalogue(p)) == p

    def test_sqlite_connect_accepts_catalogue(self, tmp_path: Path):
        cat = _ToyCatalogue(tmp_path / "toys.db")
        cat.init_db().close()
        with sqlite3.connect(cat) as conn:
            n = conn.execute("SELECT COUNT(*) FROM toys").fetchone()[0]
        conn.close()
        assert n == 0


# ---------------------------------------------------------------------------
# __enter__ / __exit__
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_with_yields_initialised_connection(self, tmp_path: Path):
        cat = _ToyCatalogue(tmp_path / "toys.db")
        with cat as conn:
            # Schema exists — init_db ran.
            n = conn.execute("SELECT COUNT(*) FROM toys").fetchone()[0]
            assert n == 0
        # Connection is CLOSED on exit (unlike sqlite3's own CM).
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_nested_with_blocks_close_their_own_conn(self, tmp_path: Path):
        cat = _ToyCatalogue(tmp_path / "toys.db")
        with cat as outer:
            with cat as inner:
                assert inner is not outer
                inner.execute("SELECT 1")
            # Inner closed; outer still usable.
            outer.execute("SELECT 1")
        with pytest.raises(sqlite3.ProgrammingError):
            outer.execute("SELECT 1")

    def test_close_happens_on_exception(self, tmp_path: Path):
        cat = _ToyCatalogue(tmp_path / "toys.db")
        with pytest.raises(RuntimeError):
            with cat as conn:
                raise RuntimeError("boom")
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


# ---------------------------------------------------------------------------
# _fetchall / _fetchone error handling
# ---------------------------------------------------------------------------


class TestReadHelperErrors:
    def test_unbuilt_schema_degrades_to_empty(self, tmp_path: Path):
        """File exists but tables don't (fresh volume / stub) -> [] not raise."""
        db = tmp_path / "stub.db"
        sqlite3.connect(db).close()  # zero-byte real file, no schema
        cat = _ToyCatalogue(db)
        assert cat._fetchall("SELECT * FROM toys") == []
        assert cat._fetchone("SELECT * FROM toys") is None

    def test_other_operational_errors_propagate(self, tmp_path: Path):
        """Non-schema faults (here: SQL syntax) must NOT be swallowed as empty."""
        cat = _ToyCatalogue(tmp_path / "toys.db")
        cat.init_db().close()
        with pytest.raises(sqlite3.OperationalError):
            cat._fetchall("SELEKT broken")
        with pytest.raises(sqlite3.OperationalError):
            cat._fetchone("SELEKT broken")


# ---------------------------------------------------------------------------
# __init_subclass__
# ---------------------------------------------------------------------------


class TestInitSubclass:
    def test_subclass_without_create_schema_rejected_at_definition(self):
        with pytest.raises(TypeError, match="_create_schema"):

            class Broken(BaseCatalogue):  # noqa: F841 — definition itself must raise
                pass

    def test_subclass_of_concrete_catalogue_inherits_schema(self, tmp_path: Path):
        # A test double subclassing a real catalogue is fine — it inherits
        # the parent's _create_schema.
        class Doubled(AACatalogue):
            pass

        Doubled(tmp_path / "x.db").init_db().close()
