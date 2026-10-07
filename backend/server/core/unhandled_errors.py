"""Outermost ASGI middleware: turn an unhandled exception into ONE log line
and a JSON 500.

Why not an ``Exception`` handler on the app: Starlette's ServerErrorMiddleware
always re-raises after calling it, so uvicorn would still dump the full
traceback (an ExceptionGroup with every BaseHTTPMiddleware frame repeated).
This middleware is added last in create_app so it runs first, catches the
exception itself, and never re-raises (unless the response has already
started). The full traceback is logged once per (path, exception type) per
``TRACEBACK_EVERY_S``; ExceptionGroups are unwrapped to their first leaf.
"""

from __future__ import annotations

import json
import logging
import traceback
from typing import Any

from backend.server.core.log_coalesce import coalescer
from backend.server.core.request_context import request_id_var

_log = logging.getLogger(__name__)

TRACEBACK_EVERY_S = 300


def leaf_exception(exc: BaseException) -> BaseException:
    """Unwrap (nested) ExceptionGroups to their first leaf."""
    seen = 0
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions and seen < 16:
        exc = exc.exceptions[0]
        seen += 1
    return exc


def app_frame(exc: BaseException) -> str:
    """``file:line in func`` of the deepest traceback frame in OUR code
    (anything not under an installed package), else the deepest frame of
    all — the one place a reader wants to open."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "?"

    def _installed(path: str) -> bool:
        return "site-packages" in path or "/lib/python" in path or "\\lib\\python" in path.lower()

    ours = [f for f in frames if not _installed(f.filename.replace("\\", "/"))]
    f = (ours or frames)[-1]
    short = f.filename.replace("\\", "/")
    for marker in ("/backend/", "/tests/"):
        if marker in short:
            short = marker.strip("/") + "/" + short.split(marker, 1)[1]
            break
    return f"{short}:{f.lineno} in {f.name}"


class UnhandledErrorMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_wrapper(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # noqa: BLE001 — this IS the catch-all
            leaf = leaf_exception(exc)
            method = scope.get("method", "?")
            path = scope.get("path", "?")
            # RequestContextMiddleware has already reset its contextvar by the
            # time the exception reaches this outermost layer, so it also
            # stashes the id on the ASGI scope state for us.
            rid = (scope.get("state") or {}).get("request_id") or request_id_var.get() or "-"
            key = f"{path}|{type(leaf).__name__}"
            with_tb, suppressed = coalescer.allow(key, TRACEBACK_EVERY_S)
            more = f" (+{suppressed} since last traceback)" if suppressed else ""
            _log.error(
                "[unhandled] %s %s -> %s: %s at %s request_id=%s%s",
                method,
                path,
                type(leaf).__name__,
                str(leaf)[:300],
                app_frame(leaf),
                rid,
                more,
                exc_info=leaf if with_tb else None,
            )
            if started:
                # Headers already on the wire — nothing sensible to send.
                raise
            body = json.dumps({"detail": "Internal Server Error", "request_id": rid}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"x-request-id", rid.encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
