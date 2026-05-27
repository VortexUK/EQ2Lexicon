"""Unit tests for ``scripts/build_zones_db.py`` parser helpers.

The encounter-bullet splitter (``_split_encounter_mobs``) is the curated
review.txt's most error-prone bit: it has three separators (comma,
``" and "``, and now the quoted-name escape), and silent splits cost
data. This file pins down the contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path

# build_zones_db.py lives under scripts/ (a non-package directory), so
# we load it by file path rather than via `import build_zones_db`.
# Otherwise pytest's collection order can fail before sys.path tweaks
# in this module take effect.
_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "build_zones_db.py"
_spec = importlib.util.spec_from_file_location("build_zones_db", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_build)
_split = _build._split_encounter_mobs


# ---------------------------------------------------------------------------
# Existing behaviour (regression guard)
# ---------------------------------------------------------------------------


def test_single_name():
    assert _split("Adkar Vyx") == ["Adkar Vyx"]


def test_comma_separated_pair():
    """The original multi-mob separator. Used by e.g. SoH 'Ire, Malevolence'."""
    assert _split("Uthtak the Cruel, Aktar the Dark") == [
        "Uthtak the Cruel",
        "Aktar the Dark",
    ]


def test_and_separated_pair():
    """'A and B' splits into two mobs — supports the 'Zarda and Kodux' style
    used by the Temple of Kor-Sha roster."""
    assert _split("Zarda and Kodux") == ["Zarda", "Kodux"]


def test_mixed_comma_and_oxford_and():
    """Curator-style 'Foo, Bar, Baz and Qux' produces four mobs — commas plus
    one trailing ' and '."""
    assert _split("Foo, Bar, Baz and Qux") == ["Foo", "Bar", "Baz", "Qux"]


def test_empty_chunks_dropped():
    """Defensive: stray double-commas don't yield empty mob names."""
    assert _split("Foo,,Bar") == ["Foo", "Bar"]


# ---------------------------------------------------------------------------
# Quoted-name escape (new)
# ---------------------------------------------------------------------------


def test_quoted_name_preserves_internal_comma():
    """Double-quoting an EQ2 canonical name with an internal comma keeps
    it atomic — this is the original motivating case:
    'Garanel Rucksif, the Cursed' is ONE mob, not two."""
    assert _split('"Garanel Rucksif, the Cursed"') == ["Garanel Rucksif, the Cursed"]


def test_quoted_name_preserves_internal_and():
    """Same escape applies to internal ' and ' — unlikely in EQ2 names
    but the rule should be consistent: quoted content is atomic."""
    assert _split('"Foo and Bar"') == ["Foo and Bar"]


def test_mixed_quoted_and_unquoted():
    """A bullet can mix atomic and split tokens. The top-level commas
    separate them; the quotes scope the no-split rule."""
    assert _split('Foo, "Bar, Baz", Qux') == ["Foo", "Bar, Baz", "Qux"]


def test_quoted_with_surrounding_whitespace():
    """The curator may indent or pad quoted names — leading/trailing
    whitespace inside the quotes is stripped, surrounding whitespace
    is irrelevant to the split."""
    assert _split('  "  Foo, Bar  "  ') == ["Foo, Bar"]


def test_unclosed_quote_emits_remainder():
    """A stray opening quote with no close mustn't silently drop the
    rest of the line — we'd lose data on a typo. Treat the unclosed
    region as one atomic mob."""
    assert _split('"Foo, Bar') == ["Foo, Bar"]


def test_empty_quoted_string_is_dropped():
    """Empty `""` between commas doesn't yield a phantom mob name."""
    assert _split('Foo, "", Bar') == ["Foo", "Bar"]


def test_single_quoted_mob_in_isolation():
    """A bullet that's ONLY a quoted name still works."""
    assert _split('"The Skeletal Lord"') == ["The Skeletal Lord"]
