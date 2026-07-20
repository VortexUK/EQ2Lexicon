"""Site-wide Census availability signal. A background loop probes Census every
5 minutes; the request/refresh paths read this (live, in-memory) state to decide
whether to attempt a refresh and what to tell the user."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import aiohttp

from backend.census.client import _redact_url as _redact_url
from backend.census.config import CENSUS_BASE_URL as _CENSUS_BASE_URL
from backend.server.config import SERVICE_ID as _SERVICE_ID

_log = logging.getLogger(__name__)

_POLL_INTERVAL = 300  # 5 minutes
# Probe a real collection (not the base `/eq2/` index) so the response is a
# normal Census JSON document we can validate. ``c:limit=1`` keeps it tiny.
_PROBE_URL = f"{_CENSUS_BASE_URL}/s:{_SERVICE_ID}/json/get/eq2/world?c:limit=1"
# Daybreak's cross-game server list; filtered to EQ2 rows client-side.
_SERVER_STATUS_URL = f"{_CENSUS_BASE_URL}/s:{_SERVICE_ID}/get/global/game_server_status?c:limit=1000"

_status: str = "unknown"  # "up" | "down" | "unknown"
_checked_at: int = 0
# {world_name: {"state": last_reported_state, "reported_at": unix}} for every
# EQ2 server in the game_server_status feed. Refreshed by the same 5-minute
# loop as the health probe.
_server_states: dict[str, dict] = {}


def _reset_for_test() -> None:
    global _status, _checked_at
    _status, _checked_at = "unknown", 0
    _server_states.clear()


def get_state() -> dict:
    return {"status": _status, "checked_at": _checked_at}


def get_server_state(world: str) -> dict | None:
    """The Census-reported state for one EQ2 server (e.g. "high", "locked",
    "down"), or None when the feed hasn't been fetched or lacks the world."""
    return _server_states.get(world.lower())


def _parse_server_states(body: dict) -> dict[str, dict]:
    """game_server_status envelope → {world_lower: {name, state, reported_at}}."""
    out: dict[str, dict] = {}
    for row in body.get("game_server_status_list", []) or []:
        if not isinstance(row, dict) or row.get("game_code") != "eq2":
            continue
        name = str(row.get("name") or "")
        if not name:
            continue
        try:
            reported_at = int(row.get("last_reported_time") or 0)
        except (TypeError, ValueError):
            reported_at = 0
        out[name.lower()] = {
            "name": name,
            "state": str(row.get("last_reported_state") or "unknown").lower(),
            "reported_at": reported_at,
        }
    return out


async def _fetch_server_states() -> None:
    """Refresh the per-server state map. Failures leave the previous map in
    place (stale beats empty for a footer indicator)."""
    global _server_states
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s, s.get(_SERVER_STATUS_URL) as r:
            if r.status != 200:
                return
            body = json.loads(await r.text())
            parsed = _parse_server_states(body)
            if parsed:
                _server_states = parsed
    except Exception as exc:
        _log.info("[census-health] server-status fetch failed: %s", exc)


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
                _log.warning(
                    "[census-health] non-JSON 200 from %s: %r",
                    _redact_url(_PROBE_URL),
                    text[:200],
                )
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
    if ok:
        # Same cadence as the probe; skipped while Census is down (the feed
        # lives on the same host, and stale-but-labelled beats churn).
        await _fetch_server_states()
    new = "up" if ok else "down"
    changed = new != _status
    _status, _checked_at = new, int(time.time())
    if changed:
        from backend.server import census_events

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
