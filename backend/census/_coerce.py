"""Low-level type-coercion helpers for Census API JSON.

Census returns most fields as strings even when they're numeric (``"42"`` not
``42``). These helpers wrap the ``int()``/``float()``/``str()`` calls + the
``None``-and-error fallbacks the codebase ended up hand-rolling in five
places (census/client.py, census/spells_db.py, census/recipes_db.py,
census/item_parser.py, census/db.py).

The leading underscore in the module name is a soft "don't import this
outside ``census/``" — the parses-side coercers in ``parses/models.py``
deliberately have different semantics (return 0 not None) for downstream
non-null fields and shouldn't migrate here.
"""

from __future__ import annotations


def coerce_int(value: object) -> int | None:
    """Coerce a Census-string-or-int to ``int | None``.

    Returns None for None, empty strings, and anything that doesn't parse
    as an int.
    """
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def coerce_float(value: object) -> float | None:
    """Coerce a Census-string-or-number to ``float | None``."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def coerce_str(value: object) -> str:
    """Coerce to ``str``, with ``None`` becoming the empty string.

    Useful for downstream string-required fields (display names, etc.)
    where None is semantically equivalent to "missing"."""
    if value is None:
        return ""
    return str(value)


def coerce_str_or_none(value: object) -> str | None:
    """Coerce to ``str | None`` — keeps the missing-vs-empty distinction.

    Strips whitespace and treats whitespace-only values as None too."""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None
