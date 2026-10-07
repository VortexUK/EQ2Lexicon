"""Per-User-Agent ingest budget from ``INGEST_CLIENT_LIMITS``
(``<prefix>=<limit>;…``, case-insensitive UA prefix match), bucketed per
(app, uploader). Counters are in-process; a restart clears them.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

from limits import RateLimitItem, parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

_log = logging.getLogger(__name__)

# A raid night is ~20-40 fights, so 30/hour still lets a *working* copy of
# the app upload normally; a retry loop hits the wall inside a minute.
DEFAULT_CLIENT_LIMITS = "EQ2AdvancedDesktop=30/hour"


@dataclass(frozen=True)
class ClientLimit:
    prefix: str  # as configured (original case, for messages)
    item: RateLimitItem


@dataclass(frozen=True)
class ThrottleVerdict:
    limited: bool
    prefix: str | None = None
    limit: str | None = None
    retry_after: int = 0  # seconds until the window frees a slot (>= 1 when limited)


def parse_client_limits(spec: str | None) -> list[ClientLimit]:
    """Parse ``prefix=limit[;prefix=limit...]`` (``;`` or ``,`` separated).

    Malformed entries are logged and skipped rather than failing startup —
    a typo in an env var must not take the ingest endpoint down. An empty
    or unset spec disables per-client throttling entirely.
    """
    out: list[ClientLimit] = []
    if not spec:
        return out
    for raw in spec.replace(",", ";").split(";"):
        entry = raw.strip()
        if not entry:
            continue
        prefix, sep, limit = entry.partition("=")
        prefix = prefix.strip()
        limit = limit.strip()
        if not sep or not prefix or not limit:
            _log.warning("[client-throttle] ignoring malformed INGEST_CLIENT_LIMITS entry %r", entry)
            continue
        try:
            item = parse(limit)
        except ValueError:
            _log.warning("[client-throttle] ignoring INGEST_CLIENT_LIMITS entry %r: bad limit %r", entry, limit)
            continue
        out.append(ClientLimit(prefix=prefix, item=item))
    return out


class ClientThrottle:
    """Moving-window budget per (client prefix, uploader identity)."""

    def __init__(self, limits: list[ClientLimit]) -> None:
        self._limits = limits
        self._storage = MemoryStorage()
        self._limiter = MovingWindowRateLimiter(self._storage)
        # Log the first rejection per window, not every one — the whole
        # point is to stop a flood from filling the logs.
        self._warned_until: dict[str, float] = {}

    @property
    def limits(self) -> list[ClientLimit]:
        return list(self._limits)

    def configure(self, limits: list[ClientLimit]) -> None:
        """Swap the rule set (tests, or a future admin endpoint) and clear
        every counter so old windows can't leak into the new rules."""
        self._limits = limits
        self.reset()

    def reset(self) -> None:
        self._storage.reset()
        self._warned_until.clear()

    def match(self, user_agent: str | None) -> ClientLimit | None:
        ua = (user_agent or "").strip().lower()
        if not ua:
            return None
        for rule in self._limits:
            if ua.startswith(rule.prefix.lower()):
                return rule
        return None

    def check(self, user_agent: str | None, identity: str, *, remote_ip: str | None = None) -> ThrottleVerdict:
        """Consume one slot for this client+identity. Returns whether the
        request must be rejected and, if so, a Retry-After in seconds."""
        rule = self.match(user_agent)
        if rule is None:
            return ThrottleVerdict(limited=False)
        key = f"{rule.prefix.lower()}|{identity}"
        if self._limiter.hit(rule.item, key):
            return ThrottleVerdict(limited=False, prefix=rule.prefix, limit=str(rule.item))
        stats = self._limiter.get_window_stats(rule.item, key)
        now = time.time()
        retry_after = max(1, math.ceil(stats.reset_time - now))
        if self._warned_until.get(key, 0.0) <= now:
            self._warned_until[key] = stats.reset_time
            _log.warning(
                "[client-throttle] %s over its ingest budget (%s) identity=%s remote_ip=%s ua=%r — "
                "rejecting with 429 until %d s from now",
                rule.prefix,
                rule.item,
                identity,
                remote_ip,
                (user_agent or "")[:80],
                retry_after,
            )
        return ThrottleVerdict(limited=True, prefix=rule.prefix, limit=str(rule.item), retry_after=retry_after)


def _build_default() -> ClientThrottle:
    # Imported lazily so this module stays importable without the server
    # config (pure unit tests construct their own ClientThrottle).
    from backend.server.config import INGEST_CLIENT_LIMITS

    return ClientThrottle(parse_client_limits(INGEST_CLIENT_LIMITS))


client_throttle = _build_default()
