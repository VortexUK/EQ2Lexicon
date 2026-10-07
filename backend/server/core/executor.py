"""Single canonical wrapper around ``loop.run_in_executor`` — use ``run_sync``
for every blocking call made from async code."""

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
    contextvars propagated to the worker thread. Positional and keyword
    arguments are both forwarded.

    The function executes inside a copy of the caller's
    ``contextvars.Context`` (as ``asyncio.to_thread`` does). This is REQUIRED
    because ``current_world()`` reads the ``_active_server`` ContextVar set by
    the per-request middleware in ``backend/server/server_context.py``;
    without propagation a thread-pool worker sees ``default_server()`` and
    silently queries the wrong world's data.

    Example:
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
