"""Cross-module DB helpers.

Two utilities that had been hand-rolled per-module across the codebase
until consolidation:

  * :func:`resolve_db_path` — locate the on-disk SQLite file for an eq2db /
    server-db module. Honours an env-var override; falls back to
    ``<repo_root>/data/<subpath>``.
  * :func:`like_escape` — escape user-supplied search strings before they
    reach a SQL ``LIKE`` so ``%`` / ``_`` literals can't broaden the
    match or force a table scan. Matching SQL must declare
    ``ESCAPE '\\'`` for the escapes to take effect.

Coercion helpers (``coerce_int`` etc.) live in
:mod:`backend.census._coerce` and are imported from there by the few
modules that need them; they predate this module and the per-module
``_int``/``_float`` duplicates that motivated this consolidation.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _repo_root() -> Path:
    """The repo root — parent of the top-level ``backend/`` package.

    Walks up from this module's location until it finds an ancestor
    that contains ``backend/``. Cached: the answer never changes during
    a process lifetime, so the walk runs at most once.
    """
    here = Path(__file__).resolve().parent
    while here.parent != here:
        if (here.parent / "backend").is_dir():
            return here.parent
        here = here.parent
    raise RuntimeError(
        f"could not locate repo root: no `backend/` directory found in any ancestor of {Path(__file__).resolve()}"
    )


def resolve_db_path(env_var: str, *subpath: str) -> Path:
    """Resolve a SQLite DB path with an env-var override.

    >>> resolve_db_path("DB_RECIPES_PATH", "recipes", "recipes.db")
    # → Path("/abs/path/to/repo/data/recipes/recipes.db") unless
    #   DB_RECIPES_PATH is set, in which case Path($DB_RECIPES_PATH).

    All eq2db / server-db modules had a `_db_path()` function with this
    exact shape, varying only in env-var name and subpath. Centralised
    here so a path-resolution bug only needs fixing once.

    Args:
      env_var: Name of the env var that overrides the default (e.g.
          ``"DB_USERS_PATH"``). When set, returns ``Path(os.getenv(env_var))``
          unchanged so a deploy can point at any absolute file.
      *subpath: Path components under ``data/`` for the default.
          ``resolve_db_path("DB_X", "items", "items.db")`` →
          ``<repo_root>/data/items/items.db``.
    """
    env = os.getenv(env_var)
    if env:
        return Path(env)
    return _repo_root() / "data" / Path(*subpath)


def like_escape(s: str) -> str:
    """Escape SQLite ``LIKE`` wildcards in user-supplied search text.

    Without this, a user typing ``foo%`` could broaden their own search
    to match everything starting with ``foo`` (the ``%`` becomes a
    wildcard), and a ``_`` could force a table scan. Backslash is
    escaped first so subsequent ``%``/``_`` escapes don't end up
    double-escaped.

    The query MUST declare ``ESCAPE '\\'`` for these escapes to take
    effect — see the ``find_by_name`` queries in items / recipes /
    spells.
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
