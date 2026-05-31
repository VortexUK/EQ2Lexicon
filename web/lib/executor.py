"""Single canonical wrapper around ``loop.run_in_executor``.

Audit BE-024: 55 grep hits for ``asyncio.get_event_loop()`` across web/,
each followed by ``await loop.run_in_executor(None, fn, *args)``. The
boilerplate was repeated literally — sometimes three times in 15 lines.

This module owns one helper. Phase 2c migrates every site.

Why not just ``asyncio.to_thread``? It accepts only positional args + kwargs
forwarded as kwargs — fine for new code, but the existing call sites
sometimes pass keyword args that would need re-shaping. ``run_sync`` accepts
both, so the migration is mechanical.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
from collections.abc import Callable
from typing import ParamSpec, TypeVar

_P = ParamSpec("_P")
_T = TypeVar("_T")


async def run_sync(fn: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:  # noqa: UP047
    """Run a synchronous function in the default executor, with the caller's
    contextvars propagated to the worker thread.

    Replaces the ``loop = asyncio.get_running_loop(); await
    loop.run_in_executor(None, fn, *args)`` boilerplate. Both positional and
    keyword arguments are forwarded — kwargs via ``functools.partial`` since
    ``run_in_executor`` only accepts positional args.

    The function executes inside a copy of the caller's
    ``contextvars.Context``. This matches ``asyncio.to_thread``'s
    documented behaviour (Python 3.9+) and is REQUIRED because the
    per-request middleware in ``web/server_context.py`` populates an
    ``_active_server`` ContextVar that ``current_world()`` reads. Without
    propagation, any DB helper dispatched via ``run_sync`` would see
    ``default_server()`` instead of the request's actual server — a
    silent data-leak-flavoured bug (the wrong world's encounters get
    queried). Production hit: 2026-05-31, /api/parses returning empty
    for Varsoon users because the thread-pool worker saw the registry's
    default server, not Varsoon.

    Example:
        result = await run_sync(parses_db.init_db)
        rows = await run_sync(parses_db.list_encounters, world="Varsoon")
    """
    loop = asyncio.get_running_loop()
    # Copy the caller's contextvars context so the worker thread sees
    # whatever was set on the asyncio task (per-server ContextVar etc.).
    # ``ctx.run`` is what asyncio.to_thread uses internally.
    ctx = contextvars.copy_context()
    # ``run_in_executor`` only accepts positional args; ``functools.partial``
    # carries our kwargs through, then ``ctx.run`` enters the captured
    # context before calling fn.
    call = functools.partial(fn, *args, **kwargs)
    return await loop.run_in_executor(None, lambda: ctx.run(call))  # type: ignore[arg-type]
