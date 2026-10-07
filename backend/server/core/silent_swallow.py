"""Context manager for intentional best-effort ``except Exception`` blocks;
logs at DEBUG so ``LOG_LEVEL=DEBUG`` surfaces the swallowed failures. Use it
instead of a bare ``except Exception: pass``."""

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
