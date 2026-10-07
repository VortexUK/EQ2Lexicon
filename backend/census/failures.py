"""Live Census failure signal — the breaker half of ``census_health``.

The 5-minute probe hits the tiny ``world`` collection, which can stay "up"
while ``character/`` times out, and every typed getter returns ``None`` for
both "failed" and "not found". This module lets the transport wrapper record
what actually happened so that (a) ``census_health.is_down()`` trips after a
burst of real failures and the store-first paths stop issuing live calls
that would surface as false 404s, and (b) non-200 responses get a WARNING
instead of a DEBUG line — coalesced, so a brown-out is one line a minute per
endpoint, not one per request.
"""

from __future__ import annotations

import threading
import time
from collections import deque

TRIP_FAILURES = 3
TRIP_WINDOW_S = 60.0

_lock = threading.Lock()
_failures: deque[float] = deque()
_last_log: dict[str, float] = {}


def note_failure(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    with _lock:
        _failures.append(now)
        while _failures and now - _failures[0] > TRIP_WINDOW_S:
            _failures.popleft()


def note_success() -> None:
    """A healthy Census response closes the breaker immediately."""
    with _lock:
        _failures.clear()


def tripped(now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    with _lock:
        while _failures and now - _failures[0] > TRIP_WINDOW_S:
            _failures.popleft()
        return len(_failures) >= TRIP_FAILURES


def should_log(key: str, every_s: float = 60.0, now: float | None = None) -> bool:
    """One WARNING per ``key`` per ``every_s`` seconds (log coalescing)."""
    now = time.monotonic() if now is None else now
    with _lock:
        last = _last_log.get(key)
        if last is not None and now - last < every_s:
            return False
        _last_log[key] = now
        return True


def _reset_for_test() -> None:
    with _lock:
        _failures.clear()
        _last_log.clear()
