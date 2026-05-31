"""
Shared rate-limiter instance.

Import `limiter` in route modules and apply @limiter.limit("N/minute")
decorators to endpoints that trigger expensive downstream calls (Census API,
SQLite searches).  The limiter is keyed by client IP address.

The instance must also be registered on the FastAPI app in web/app.py:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=[])
