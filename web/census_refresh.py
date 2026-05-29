"""Background refresh orchestration for census-backed lookups. The ONLY place
that triggers a Census call from the web layer. Throttled (>=15 min between
attempts per entity), deduped (one in-flight per key), and skipped entirely when
Census health is down. On success: merge into census_store, update the hot
in-memory cache, and publish an SSE record event."""

from __future__ import annotations

import asyncio
import logging
import time

from census import census_store
from census.client import CensusClient
from web import census_events, census_health
from web.cache import character_cache
from web.config import SERVICE_ID as _SERVICE_ID
from web.server_context import current_world

_log = logging.getLogger(__name__)


def _scrub(value: object) -> str:
    """Strip CR/LF before logging a user-supplied value, so a crafted name
    can't forge log lines (CWE-117 log injection)."""
    return str(value).replace("\r", " ").replace("\n", " ")


_THROTTLE = 900  # 15 minutes between refresh attempts per entity
_last_attempt: dict[str, float] = {}
_in_flight: set[str] = set()


def _reset_for_test() -> None:
    _last_attempt.clear()
    _in_flight.clear()


def _should_refresh(key: str) -> bool:
    if census_health.is_down():
        return False
    if key in _in_flight:
        return False
    last = _last_attempt.get(key)
    return last is None or (time.monotonic() - last) >= _THROTTLE


def _mark_attempt(key: str) -> None:
    _last_attempt[key] = time.monotonic()


def request_character_refresh(name: str) -> None:
    """Fire-and-forget a throttled background character refresh."""
    world = current_world()
    key = f"{name.lower()}:{world.lower()}"
    if not _should_refresh(key):
        return
    _mark_attempt(key)
    _in_flight.add(key)
    asyncio.create_task(_run_character_refresh(name, key, world))


async def _run_character_refresh(name: str, key: str, world: str) -> None:
    from web.routes.character import _build_char_response  # local: avoid import cycle

    try:
        # CENSUS-CLIENT-LIFECYCLE: migrate to web.lib.census_lifecycle.shared_census_client (Phase 2c.2)
        client = CensusClient(service_id=_SERVICE_ID)
        try:
            char = await client.get_character(name, world)
        finally:
            await client.close()
        if char is None:
            return  # not found / not resolved → keep best-known
        resp = _build_char_response(char)  # CharacterResponse (pydantic)
        data = resp.model_dump()
        resolved = bool(data.get("cls") or data.get("level"))
        conn = census_store.init_db(census_store.DB_PATH)
        try:
            census_store.upsert_character(conn, name, world, data, resolved=resolved)
        finally:
            conn.close()
        if resolved:
            character_cache.set(key, resp)
            census_events.publish({"type": "character", "key": key, "data": data, "fetched_at": int(time.time())})
    except Exception as exc:
        _log.warning("[census-refresh] character %s failed: %s", _scrub(name), exc)
    finally:
        _in_flight.discard(key)


def _merge_roster(roster: list[dict], fresh: dict[str, dict], stored: dict[str, dict]) -> list[dict]:
    """Build the displayed roster: each member's rank from the (reliable) roster
    list, merged with the best-known per-member data — fresh resolve if present,
    else the stored character record (keyed by lower-cased name)."""
    out: list[dict] = []
    for m in roster:
        name = m["name"]
        data = fresh.get(name) or stored.get(name.lower()) or {}
        if not data:
            # Neither fresh nor stored — never seen online. Omit rather than
            # emit a blank row (no level/class).
            continue
        out.append({**data, "name": name, "rank": m.get("rank"), "rank_id": m.get("rank_id")})
    return out


def request_guild_refresh(name: str) -> None:
    world = current_world()
    key = f"guild:{name.lower()}:{world.lower()}"
    if not _should_refresh(key):
        return
    _mark_attempt(key)
    _in_flight.add(key)
    asyncio.create_task(_run_guild_refresh(name, key, world))


async def _run_guild_refresh(name: str, key: str, world: str) -> None:
    from web.routes.guild import _persist_and_publish_guild

    try:
        await _persist_and_publish_guild(name, world)
    except Exception as exc:
        _log.warning("[census-refresh] guild %s failed: %s", _scrub(name), exc)
    finally:
        _in_flight.discard(key)
