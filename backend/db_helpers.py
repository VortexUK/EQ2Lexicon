"""Cross-module DB helpers.

One utility that had been hand-rolled per-module across the codebase until
consolidation:

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


def like_escape(s: str) -> str:
    """Escape SQL ``LIKE`` wildcards in user-supplied search text.

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
