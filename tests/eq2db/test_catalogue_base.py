"""Tests for the SQLite base-class dunder surface (backend/db_catalogue.py:
PathBound / BaseCatalogue — still the base for items/spells/recipes/aas/classes).

Exercised through RecipeCatalogue (no caches) and SpellCatalogue (crc cache)
so the behaviour is proven on real subclasses, not a synthetic stub.
(RaidCatalogue/ZoneCatalogue were the original concrete examples but moved
to PgCatalogue in the Postgres cutover — same intent, SQLite-resident
catalogues now carry the cases.)
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from backend.db_catalogue import BaseCatalogue
from backend.eq2db.recipes import RecipeCatalogue
from backend.eq2db.spells import SpellCatalogue

# ---------------------------------------------------------------------------
# __repr__ / _cache_info
# ---------------------------------------------------------------------------


class TestRepr:
    def test_missing_db(self, tmp_path: Path):
        cat = RecipeCatalogue(tmp_path / "recipes.db")
        r = repr(cat)
        assert r.startswith("RecipeCatalogue(")
        assert "missing" in r
        # !r-escaped on Windows, so compare against the repr of the string
        assert repr(str(tmp_path / "recipes.db")) in r

    def test_ready_db(self, tmp_path: Path):
        cat = RecipeCatalogue(tmp_path / "recipes.db")
        cat.init_db().close()
        assert "ready" in repr(cat)

    def test_cache_sizes_rendered(self, tmp_path: Path):
        cat = SpellCatalogue(tmp_path / "spells.db")
        assert "crc_cache=0" in repr(cat)
        cat._crc_cache[(123, None)] = None
        assert "crc_cache=1" in repr(cat)

    def test_no_cache_subclass_has_no_cache_suffix(self, tmp_path: Path):
        r = repr(RecipeCatalogue(tmp_path / "recipes.db"))
        assert r.endswith("missing)")


# ---------------------------------------------------------------------------
# __bool__
# ---------------------------------------------------------------------------


class TestBool:
    def test_false_when_db_missing(self, tmp_path: Path):
        assert not RecipeCatalogue(tmp_path / "nope.db")

    def test_true_when_db_exists(self, tmp_path: Path):
        cat = RecipeCatalogue(tmp_path / "recipes.db")
        cat.init_db().close()
        assert cat


# ---------------------------------------------------------------------------
# __eq__ / __hash__
# ---------------------------------------------------------------------------


class TestEqHash:
    def test_same_class_same_path_equal(self, tmp_path: Path):
        p = tmp_path / "recipes.db"
        assert RecipeCatalogue(p) == RecipeCatalogue(p)
        assert hash(RecipeCatalogue(p)) == hash(RecipeCatalogue(p))

    def test_different_path_not_equal(self, tmp_path: Path):
        assert RecipeCatalogue(tmp_path / "a.db") != RecipeCatalogue(tmp_path / "b.db")

    def test_different_class_same_path_not_equal(self, tmp_path: Path):
        p = tmp_path / "x.db"
        assert RecipeCatalogue(p) != SpellCatalogue(p)

    def test_non_catalogue_not_equal(self, tmp_path: Path):
        assert RecipeCatalogue(tmp_path / "x.db") != "x.db"

    def test_usable_as_dict_key(self, tmp_path: Path):
        p = tmp_path / "recipes.db"
        d = {RecipeCatalogue(p): "hit"}
        assert d[RecipeCatalogue(p)] == "hit"


# ---------------------------------------------------------------------------
# __fspath__
# ---------------------------------------------------------------------------


class TestFspath:
    def test_os_fspath(self, tmp_path: Path):
        p = tmp_path / "recipes.db"
        assert os.fspath(RecipeCatalogue(p)) == str(p)

    def test_path_conversion(self, tmp_path: Path):
        p = tmp_path / "recipes.db"
        assert Path(RecipeCatalogue(p)) == p

    def test_sqlite_connect_accepts_catalogue(self, tmp_path: Path):
        cat = RecipeCatalogue(tmp_path / "recipes.db")
        cat.init_db().close()
        with sqlite3.connect(cat) as conn:
            n = conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
        conn.close()
        assert n == 0


# ---------------------------------------------------------------------------
# __enter__ / __exit__
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_with_yields_initialised_connection(self, tmp_path: Path):
        cat = RecipeCatalogue(tmp_path / "recipes.db")
        with cat as conn:
            # Schema exists — init_db ran.
            n = conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
            assert n == 0
        # Connection is CLOSED on exit (unlike sqlite3's own CM).
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_nested_with_blocks_close_their_own_conn(self, tmp_path: Path):
        cat = RecipeCatalogue(tmp_path / "recipes.db")
        with cat as outer:
            with cat as inner:
                assert inner is not outer
                inner.execute("SELECT 1")
            # Inner closed; outer still usable.
            outer.execute("SELECT 1")
        with pytest.raises(sqlite3.ProgrammingError):
            outer.execute("SELECT 1")

    def test_close_happens_on_exception(self, tmp_path: Path):
        cat = RecipeCatalogue(tmp_path / "recipes.db")
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
        cat = RecipeCatalogue(db)
        assert cat._fetchall("SELECT * FROM recipes") == []
        assert cat._fetchone("SELECT * FROM recipes") is None

    def test_other_operational_errors_propagate(self, tmp_path: Path):
        """Non-schema faults (here: SQL syntax) must NOT be swallowed as empty."""
        cat = RecipeCatalogue(tmp_path / "recipes.db")
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
        class Doubled(RecipeCatalogue):
            pass

        Doubled(tmp_path / "x.db").init_db().close()
