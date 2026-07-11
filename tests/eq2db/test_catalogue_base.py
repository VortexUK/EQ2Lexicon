"""Tests for the BaseCatalogue dunder surface (backend/eq2db/_catalogue.py).

Exercised through RaidCatalogue (no caches, FOREIGN_KEYS=True) and
SpellCatalogue (crc cache) so the behaviour is proven on real subclasses,
not a synthetic stub.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from backend.db_catalogue import BaseCatalogue
from backend.eq2db.raids import RaidCatalogue
from backend.eq2db.spells import SpellCatalogue

# ---------------------------------------------------------------------------
# __repr__ / _cache_info
# ---------------------------------------------------------------------------


class TestRepr:
    def test_missing_db(self, tmp_path: Path):
        cat = RaidCatalogue(tmp_path / "raids.db")
        r = repr(cat)
        assert r.startswith("RaidCatalogue(")
        assert "missing" in r
        # !r-escaped on Windows, so compare against the repr of the string
        assert repr(str(tmp_path / "raids.db")) in r

    def test_ready_db(self, tmp_path: Path):
        cat = RaidCatalogue(tmp_path / "raids.db")
        cat.init_db().close()
        assert "ready" in repr(cat)

    def test_cache_sizes_rendered(self, tmp_path: Path):
        cat = SpellCatalogue(tmp_path / "spells.db")
        assert "crc_cache=0" in repr(cat)
        cat._crc_cache[(123, None)] = None
        assert "crc_cache=1" in repr(cat)

    def test_no_cache_subclass_has_no_cache_suffix(self, tmp_path: Path):
        r = repr(RaidCatalogue(tmp_path / "raids.db"))
        assert r.endswith("missing)")


# ---------------------------------------------------------------------------
# __bool__
# ---------------------------------------------------------------------------


class TestBool:
    def test_false_when_db_missing(self, tmp_path: Path):
        assert not RaidCatalogue(tmp_path / "nope.db")

    def test_true_when_db_exists(self, tmp_path: Path):
        cat = RaidCatalogue(tmp_path / "raids.db")
        cat.init_db().close()
        assert cat


# ---------------------------------------------------------------------------
# __eq__ / __hash__
# ---------------------------------------------------------------------------


class TestEqHash:
    def test_same_class_same_path_equal(self, tmp_path: Path):
        p = tmp_path / "raids.db"
        assert RaidCatalogue(p) == RaidCatalogue(p)
        assert hash(RaidCatalogue(p)) == hash(RaidCatalogue(p))

    def test_different_path_not_equal(self, tmp_path: Path):
        assert RaidCatalogue(tmp_path / "a.db") != RaidCatalogue(tmp_path / "b.db")

    def test_different_class_same_path_not_equal(self, tmp_path: Path):
        p = tmp_path / "x.db"
        assert RaidCatalogue(p) != SpellCatalogue(p)

    def test_non_catalogue_not_equal(self, tmp_path: Path):
        assert RaidCatalogue(tmp_path / "x.db") != "x.db"

    def test_usable_as_dict_key(self, tmp_path: Path):
        p = tmp_path / "raids.db"
        d = {RaidCatalogue(p): "hit"}
        assert d[RaidCatalogue(p)] == "hit"


# ---------------------------------------------------------------------------
# __fspath__
# ---------------------------------------------------------------------------


class TestFspath:
    def test_os_fspath(self, tmp_path: Path):
        p = tmp_path / "raids.db"
        assert os.fspath(RaidCatalogue(p)) == str(p)

    def test_path_conversion(self, tmp_path: Path):
        p = tmp_path / "raids.db"
        assert Path(RaidCatalogue(p)) == p

    def test_sqlite_connect_accepts_catalogue(self, tmp_path: Path):
        cat = RaidCatalogue(tmp_path / "raids.db")
        cat.init_db().close()
        with sqlite3.connect(cat) as conn:
            n = conn.execute("SELECT COUNT(*) FROM raid_zones").fetchone()[0]
        conn.close()
        assert n == 0


# ---------------------------------------------------------------------------
# __enter__ / __exit__
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_with_yields_initialised_connection(self, tmp_path: Path):
        cat = RaidCatalogue(tmp_path / "raids.db")
        with cat as conn:
            # Schema exists — init_db ran.
            n = conn.execute("SELECT COUNT(*) FROM raid_encounters").fetchone()[0]
            assert n == 0
        # Connection is CLOSED on exit (unlike sqlite3's own CM).
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_nested_with_blocks_close_their_own_conn(self, tmp_path: Path):
        cat = RaidCatalogue(tmp_path / "raids.db")
        with cat as outer:
            with cat as inner:
                assert inner is not outer
                inner.execute("SELECT 1")
            # Inner closed; outer still usable.
            outer.execute("SELECT 1")
        with pytest.raises(sqlite3.ProgrammingError):
            outer.execute("SELECT 1")

    def test_close_happens_on_exception(self, tmp_path: Path):
        cat = RaidCatalogue(tmp_path / "raids.db")
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
        cat = RaidCatalogue(db)
        assert cat._fetchall("SELECT * FROM raid_zones") == []
        assert cat._fetchone("SELECT * FROM raid_zones") is None

    def test_other_operational_errors_propagate(self, tmp_path: Path):
        """Non-schema faults (here: SQL syntax) must NOT be swallowed as empty."""
        cat = RaidCatalogue(tmp_path / "raids.db")
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
        class Doubled(RaidCatalogue):
            pass

        Doubled(tmp_path / "x.db").init_db().close()
