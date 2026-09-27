"""Coalesce repeated log events so a flood produces one line per window.

A misbehaving client on 2026-09-27 turned three log statements into ~15,000
lines in an hour: slowapi's per-request "ratelimit exceeded" warning, our
own 422 line, and a 250-line framework traceback per 500. None of the
repeats carried new information. ``Coalescer.allow`` answers "should THIS
occurrence be logged?" for a key, letting one line through per window and
reporting how many were swallowed since, so the next logged line can say
"(+N suppressed)". Process-local, unbounded only by distinct keys — callers
must key on something with low cardinality (path + exception type, path +
client identity), never on raw request data.
"""

from __future__ import annotations

import threading
import time


class Coalescer:
    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (next_allowed_at, suppressed_since_last_log)
        self._state: dict[str, tuple[float, int]] = {}

    def allow(self, key: str, every_s: float) -> tuple[bool, int]:
        """Return ``(log_it, suppressed)``. ``suppressed`` is the number of
        occurrences swallowed since the last one that was logged; it is
        only non-zero when ``log_it`` is True (so it can go in that line)."""
        now = self._clock()
        with self._lock:
            next_at, suppressed = self._state.get(key, (0.0, 0))
            if now >= next_at:
                self._state[key] = (now + every_s, 0)
                return True, suppressed
            self._state[key] = (next_at, suppressed + 1)
            return False, 0

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


#: Shared instance for the app's request-path handlers (tests reset it).
coalescer = Coalescer()
