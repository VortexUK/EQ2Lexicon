"""User-requested Census refresh queue.

The "Refresh from Census" buttons on character/guild pages funnel through
ONE global FIFO with a paced worker (app.py lifespan task) — no matter how
many users click, Census sees at most one manual refresh per
``WORKER_PACING_S``. Jobs reuse the census_refresh runners, so a completed
refresh merges into census_store, updates the hot cache, and publishes the
SSE record event the pages already live-swap on.

Spam mitigations (the feature spec):
  - per entity: at most ``PER_ENTITY_PER_HOUR`` manual refreshes per hour
  - per user:   one job in the queue at a time
  - global:     bounded queue; requests refused while Census health is down

State is process-local (WEB_CONCURRENCY=1 — same contract as the SSE bus
and the LRU caches). ``status()`` is what the frontend polls so a queued
spinner survives page reloads.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from backend.core.log_safety import scrub as _scrub
from backend.server import census_health, census_refresh

_log = logging.getLogger(__name__)

#: Seconds between manual jobs — the global pacing toward Census.
WORKER_PACING_S = 5

#: Manual refreshes allowed per entity per rolling hour.
PER_ENTITY_PER_HOUR = 4

#: Hard queue bound — beyond this something is being scripted.
MAX_QUEUE = 200


class Refused(Exception):
    """An enqueue that was declined, with a machine reason + human detail."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass
class _Job:
    kind: str  # "character" | "guild"
    name: str
    world: str
    key: str
    requested_by: str


_queue: asyncio.Queue[_Job] = asyncio.Queue()
_pending: dict[str, _Job] = {}  # key -> job, queued OR currently running
_running: str | None = None
_by_user: dict[str, str] = {}  # discord_id -> their pending key
_entity_history: dict[str, list[float]] = {}  # key -> recent enqueue times (monotonic)


def _reset_for_test() -> None:
    while not _queue.empty():
        _queue.get_nowait()
    _pending.clear()
    _by_user.clear()
    _entity_history.clear()
    global _running
    _running = None


def _key_for(kind: str, name: str, world: str) -> str:
    return f"{kind}:{name.lower()}:{world.lower()}"


def status(kind: str, name: str, world: str) -> dict:
    """{queued, running} for the page's spinner — queued stays True while
    the job is anywhere in the pipeline (including mid-run)."""
    key = _key_for(kind, name, world)
    return {"queued": key in _pending, "running": _running == key}


def enqueue(kind: str, name: str, world: str, user_id: str) -> dict:
    """Queue a manual refresh. Raises :class:`Refused` when a mitigation
    declines it; re-clicking an already-queued entity is idempotent."""
    key = _key_for(kind, name, world)
    if key in _pending:
        return {"queued": True, "already_queued": True}
    if census_health.is_down():
        raise Refused("census_down", "Census is currently unavailable — try again shortly.")
    user_key = _by_user.get(user_id)
    if user_key is not None and user_key in _pending:
        raise Refused("user_has_queued", "You already have a refresh queued — wait for it to finish.")
    now = time.monotonic()
    history = [t for t in _entity_history.get(key, []) if now - t < 3600]
    if len(history) >= PER_ENTITY_PER_HOUR:
        raise Refused(
            "entity_rate",
            f"This {kind} has already been refreshed {PER_ENTITY_PER_HOUR} times in the last hour.",
        )
    if _queue.qsize() >= MAX_QUEUE:
        raise Refused("queue_full", "The refresh queue is full — try again in a few minutes.")

    history.append(now)
    _entity_history[key] = history
    job = _Job(kind=kind, name=name, world=world, key=key, requested_by=user_id)
    _pending[key] = job
    _by_user[user_id] = key
    _queue.put_nowait(job)
    return {"queued": True, "already_queued": False}


async def _process(job: _Job) -> None:
    if job.kind == "character":
        await census_refresh.run_character_refresh_now(job.name, job.world)
    else:
        await census_refresh.run_guild_refresh_now(job.name, job.world)


async def worker_loop() -> None:
    """Lifespan task: drain the queue one job at a time with global pacing."""
    global _running
    while True:
        job = await _queue.get()
        _running = job.key
        started = time.monotonic()
        try:
            await _process(job)
            _log.info(
                "[refresh-queue] %s %s refreshed in %.1fs (requested by %s)",
                job.kind,
                _scrub(job.name),
                time.monotonic() - started,
                job.requested_by,
            )
        except Exception:
            _log.exception("[refresh-queue] %s %s failed", job.kind, _scrub(job.name))
        finally:
            _running = None
            _pending.pop(job.key, None)
            if _by_user.get(job.requested_by) == job.key:
                _by_user.pop(job.requested_by, None)
        await asyncio.sleep(WORKER_PACING_S)
