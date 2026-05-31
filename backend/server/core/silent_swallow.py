"""Intent-marking context manager for "swallow + keep going" exception paths.

Audit BE-080: 22 ``except Exception: pass`` sites. About half are intentional
(metrics increments / cache-write best-effort), half are bugs hiding behind
the silent swallow (a malformed JSON in data/AAs/trees silently disappears
from the index; a Pydantic error in _overview_to_char_response silently drops
a guild member from the cache).

The fix is two-step:
  1. Phase 2a (this task): create a ``swallow(category)`` context manager so
     the intentional sites have a named, log-emitting alternative.
  2. Phase 2c.4: walk every existing ``except Exception: pass`` site, decide
     whether it's intentional or bug-shaped, refactor accordingly.

Even the "intentional" sites benefit — a real failure inside a metric-
increment block today is completely invisible. ``swallow`` logs at DEBUG so
``LOG_LEVEL=DEBUG`` surfaces it on demand.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

_log = logging.getLogger(__name__)


@contextmanager
def swallow(category: str, *, level: int = logging.DEBUG) -> Iterator[None]:
    """Context manager that swallows ``Exception`` and logs at ``level``.

    ``category`` is a short string (e.g. ``"metrics"``, ``"cache-write"``)
    that identifies the intent of the swallow — surfaces in the log message
    so a contributor grepping for the failure has a fighting chance.

    Use only where the work is genuinely best-effort (metrics increments,
    cache writes, opportunistic enrichment). For real "the caller might not
    care but we should still know" sites, log at WARNING via a regular
    ``try/except Exception as exc: _log.warning(...)`` block instead.

    Example::

        with swallow("metrics"):
            CACHE_HITS.labels(cache="character").inc()
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 — this IS the catch-all helper
        _log.log(level, "[swallow:%s] %s: %r", category, type(exc).__name__, exc)
