"""Site-wide Census availability signal. A background loop probes Census every
5 minutes; the request/refresh paths read this (live, in-memory) state to decide
whether to attempt a refresh and what to tell the user."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import aiohttp

from census.config import CENSUS_BASE_URL as _CENSUS_BASE_URL
from web.config import SERVICE_ID as _SERVICE_ID

_log = logging.getLogger(__name__)

_POLL_INTERVAL = 300  # 5 minutes
# Probe a real collection (not the base `/eq2/` index) so the response is a
# normal Census JSON document we can validate. ``c:limit=1`` keeps it tiny.
_PROBE_URL = f"{_CENSUS_BASE_URL}/s:{_SERVICE_ID}/json/get/eq2/world?c:limit=1"

_status: str = "unknown"  # "up" | "down" | "unknown"
_checked_at: int = 0


def _reset_for_test() -> None:
    global _status, _checked_at
    _status, _checked_at = "unknown", 0


def get_state() -> dict:
    return {"status": _status, "checked_at": _checked_at}


def is_down() -> bool:
    return _status == "down"


def _body_looks_healthy(body: dict) -> bool:
    """A Census response counts as healthy only if it's:

      * a JSON object, and
      * has NO ``errorCode`` field at the top level, and
      * has a non-negative ``returned`` count (the standard envelope field
        for collection queries — present on every working response).

    During Census outages we've observed `200 OK` with a body like
    ``{"errorCode":"SERVER_ERROR"}`` (no ``returned`` field), so the
    status-code-only check used to false-positive as healthy. This is the
    minimum body validation needed to catch that.
    """
    if not isinstance(body, dict):
        return False
    if "errorCode" in body:
        return False
    return body.get("returned", -1) >= 0


async def _probe_census() -> bool:
    """True iff Census responds 200 AND its body parses as a healthy Census
    envelope (no ``errorCode`` field, ``returned`` present). See
    ``_body_looks_healthy`` for the validation rules."""
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s, s.get(_PROBE_URL) as r:
            if r.status != 200:
                return False
            text = await r.text()
            try:
                body = json.loads(text)
            except json.JSONDecodeError:
                _log.warning("[census-health] non-JSON 200 response: %r", text[:200])
                return False
            return _body_looks_healthy(body)
    except Exception as exc:
        _log.info("[census-health] Probe failed: %s", exc)
        return False


async def refresh_health() -> str:
    """Probe once, update state, return the new status. Publishes an SSE health
    event on change (import is local to avoid a cycle)."""
    global _status, _checked_at
    ok = await _probe_census()
    new = "up" if ok else "down"
    changed = new != _status
    _status, _checked_at = new, int(time.time())
    if changed:
        from web import census_events

        census_events.publish({"type": "health", "status": _status, "checked_at": _checked_at})
    return _status


async def poll_loop() -> None:
    """Background task: probe now, then every 5 minutes. Never raises."""
    while True:
        try:
            await refresh_health()
        except Exception:  # pragma: no cover - defensive
            _log.exception("[census-health] probe error")
        await asyncio.sleep(_POLL_INTERVAL)
