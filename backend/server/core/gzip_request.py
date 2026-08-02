"""Transparent gzip request-body decompression (pure ASGI middleware).

The ACT plugin (v0.1.16+) gzips its ingest payloads — ACT JSON compresses
10-20×, which is the difference between a 2 s and a 20+ s upload on a slow
route (2026-07-28 incident: a raider on a ~120 kbit/s effective route could
not push a 282 KB payload inside the plugin's HttpClient timeout).

When a request carries ``Content-Encoding: gzip``, the middleware drains the
body, decompresses it (bounded — zip-bomb guard), strips the header, fixes
``Content-Length``, and hands the plain body downstream. Everything after it
— FastAPI's model parsing AND the HMAC check's ``request.body()`` — sees the
uncompressed bytes, so the plugin's signature contract (HMAC over the
uncompressed JSON) holds for both compressed and plain uploads. Requests
without the header pass through completely untouched, so pre-gzip plugin
versions keep working unchanged.
"""

from __future__ import annotations

import gzip
import json
import logging
import zlib

_log = logging.getLogger(__name__)

# Decompressed-size cap. The plugin's own payload cap is 10 MiB of JSON;
# anything bigger than this after decompression is hostile or broken.
MAX_DECOMPRESSED_BYTES = 16 * 1024 * 1024

# Raw-request-body cap enforced BEFORE the body is buffered/parsed. The plugin
# caps its JSON at 10 MiB and a gzipped upload is 10-20x smaller, so 12 MiB of
# raw wire bytes comfortably fits every legitimate upload while stopping a
# plain (non-gzip) multi-hundred-MB POST from being buffered into memory. The
# gzip path stays additionally bounded by MAX_DECOMPRESSED_BYTES above.
MAX_REQUEST_BODY_BYTES = 12 * 1024 * 1024


def _plain_response(send, status: int, detail: str):
    body = json.dumps({"detail": detail}).encode()

    async def _send() -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return _send()


class BodySizeLimitMiddleware:
    """Reject over-large request bodies before they are buffered.

    Register OUTERMOST of the body-touching middlewares (after
    GzipRequestMiddleware in add_middleware order) so it caps the RAW incoming
    bytes — a gzip payload is checked at its small compressed size, a plain
    payload at its full size. Content-Length is the fast path; a request
    without a usable Content-Length (chunked) is drained up to the cap and
    replayed, so the buffer can never exceed the cap either way.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        cl = headers.get(b"content-length")
        if cl is not None:
            try:
                declared = int(cl)
            except ValueError:
                declared = None
            if declared is not None:
                if declared > MAX_REQUEST_BODY_BYTES:
                    await _plain_response(send, 413, "Request body too large.")
                    return
                # Trustworthy length within the cap — pass straight through.
                await self.app(scope, receive, send)
                return

        # No usable Content-Length (chunked). Drain up to the cap, reject if
        # exceeded, then replay the buffered body downstream.
        chunks: list[bytes] = []
        total = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > MAX_REQUEST_BODY_BYTES:
                await _plain_response(send, 413, "Request body too large.")
                return
            chunks.append(body)
            more = message.get("more_body", False)
        buffered = b"".join(chunks)

        sent = False

        async def replay_receive():
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": buffered, "more_body": False}

        await self.app(scope, replay_receive, send)


class GzipRequestMiddleware:
    """Decompress gzip-encoded request bodies before anything reads them."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        if headers.get(b"content-encoding", b"").strip().lower() != b"gzip":
            await self.app(scope, receive, send)
            return

        # Drain the (compressed) body.
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return  # client went away mid-upload; nothing to serve
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break

        try:
            # gzip.decompress inflates fully in one go — enforce the cap by
            # streaming through a zlib decompressor with a max_length bound.
            d = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)  # gzip container
            body = d.decompress(b"".join(chunks), MAX_DECOMPRESSED_BYTES)
            if d.unconsumed_tail:
                await _plain_response(send, 413, "Decompressed payload too large.")
                return
            d.flush()
        except (zlib.error, gzip.BadGzipFile, EOFError) as exc:
            _log.warning("[gzip-request] bad gzip body on %s: %s", scope.get("path"), exc)
            await _plain_response(send, 400, "Request body is not valid gzip.")
            return

        # Downstream must see a plain request: drop content-encoding, fix
        # content-length, and replay the decompressed body as one message.
        scope = dict(scope)
        scope["headers"] = [
            (k, v) for k, v in scope["headers"] if k != b"content-encoding" and k != b"content-length"
        ] + [(b"content-length", str(len(body)).encode())]

        sent = False

        async def replay_receive():
            nonlocal sent
            if sent:
                return await receive()  # any follow-up (http.disconnect)
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_receive, send)
