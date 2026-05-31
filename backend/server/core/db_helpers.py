"""Shared SQLite read-helpers for the local catalogue databases.

Every `census/*_db.py` module repeats the same opening dance: check
``path.exists()``, open with ``sqlite3.connect``, set ``row_factory =
sqlite3.Row``, close on exit. The fallback for missing DB is also
identical — either return None (find_by_*) or empty list (list_*).

This module owns the connection lifecycle + the missing-DB fallback as a
decorator. The bespoke SQL stays per-module — only the boilerplate moves.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

_P = ParamSpec("_P")
_T = TypeVar("_T")


@contextmanager
def read_only_conn(path: Path):  # type: ignore[return]
    """Open a read-only SQLite connection with ``sqlite3.Row`` factory.

    Uses URI mode ``file:<path>?mode=ro`` so the connection can't accidentally
    write — useful for the metrics scraper and the rankings query path
    where any write would be a bug. Caller must check ``path.exists()``
    first; opening a ro connection to a non-existent file raises
    ``sqlite3.OperationalError("unable to open database file")``.
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def fallback_if_missing(
    path_attr: str,
    default: Any,
) -> Callable[[Callable[_P, _T]], Callable[_P, _T | Any]]:
    """Decorator: short-circuit a sync DB helper with ``default`` when the DB
    file is missing.

    ``path_attr``: module attribute name (e.g. ``"DB_PATH"``) used to resolve
    the file location on each call. Looked up on the decorated function's
    defining module, so the helper picks up env-var-driven re-paths during
    tests without rebinding.

    Usage::

        @fallback_if_missing("DB_PATH", [])
        def list_xxx(...) -> list[dict]:
            with read_only_conn(DB_PATH) as conn:
                ...
    """

    def _decorator(fn: Callable[_P, _T]) -> Callable[_P, _T | Any]:
        @wraps(fn)
        def _wrapper(*args: _P.args, **kwargs: _P.kwargs):  # noqa: UP047
            module = __import__(fn.__module__, fromlist=[path_attr])
            path = getattr(module, path_attr)
            if not path.exists():
                return default
            return fn(*args, **kwargs)

        return _wrapper

    return _decorator


def like_escape(s: str) -> str:
    """Escape SQLite ``LIKE`` wildcards so a user-supplied search string can't
    silently broaden the match (``%``) or force a table scan (``_``).

    The matching SQL must use ``ESCAPE '\\'`` for these escapes to take
    effect. Consolidates the per-module ``_like_escape`` helpers added in
    Phase 1 Task 1.3.
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
