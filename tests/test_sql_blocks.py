"""Meta-test: every ``_SQL["name"]`` reference resolves in its .sql sidecar.

A store conversion once dropped a named block while editing the comment
above it (claims.sql, select_claim_user_and_world) — the failure only
surfaced when that exact code path ran. This pins the whole repo: any
reference to a missing block fails collection-fast, dialect-free.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.sql_loader import parse_sql

_REPO = Path(__file__).resolve().parents[1]
_REF_RE = re.compile(r'_SQL\["([a-z0-9_]+)"\]')


def _module_sql_pairs() -> list[tuple[Path, Path]]:
    pairs = []
    for sql_path in sorted((_REPO / "backend").rglob("*.sql")):
        py_path = sql_path.with_suffix(".py")
        if py_path.is_file():
            pairs.append((py_path, sql_path))
    return pairs


@pytest.mark.parametrize(
    ("py_path", "sql_path"),
    _module_sql_pairs(),
    ids=lambda p: str(p.relative_to(_REPO)) if isinstance(p, Path) else str(p),
)
def test_every_sql_reference_resolves(py_path: Path, sql_path: Path) -> None:
    refs = set(_REF_RE.findall(py_path.read_text(encoding="utf-8")))
    blocks = set(parse_sql(sql_path.read_text(encoding="utf-8")))
    missing = refs - blocks
    assert not missing, f"{py_path.name} references missing SQL blocks: {sorted(missing)}"


#: Sidecar directories outside the scoped format-field check below.
_SQLITE_FAMILIES = {
    "backend/eq2db",
    "backend/census",
    "backend/server/parses",
    "backend/server/api",
    "backend/image",
    "backend/bot",
}

_PG_DIRS = ("backend/server/db",)


def test_no_format_fields_inside_pg_block_bodies() -> None:
    """A ``-- … {field}`` comment that SURVIVES block parsing (i.e. is not
    a trailing comment the loader trims) gets str.format()ed with the body;
    the substituted %s inside the comment then counts as a psycopg
    placeholder (psycopg does not strip comments client-side) and breaks
    the parameter count — claims.sql list_claims shipped exactly this.
    Scoped to the Postgres-dialect sidecars."""
    offenders: list[str] = []
    for _, sql_path in _module_sql_pairs():
        rel = sql_path.relative_to(_REPO).as_posix()
        if not rel.startswith(_PG_DIRS):
            continue
        blocks = parse_sql(sql_path.read_text(encoding="utf-8"))
        for name, body in blocks.items():
            for line in body.splitlines():
                stripped = line.strip()
                if stripped.startswith("--") and re.search(r"\{[a-z_]+\}", stripped):
                    offenders.append(f"{sql_path.name} block {name!r}: {stripped!r}")
    assert not offenders, f"format-field comments inside parsed PG block bodies: {offenders}"
