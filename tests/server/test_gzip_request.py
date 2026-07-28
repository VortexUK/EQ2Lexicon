"""Tests for GzipRequestMiddleware — transparent request-body decompression.

The ACT plugin (v0.1.16+) gzips ingest payloads and signs the UNCOMPRESSED
JSON; the middleware inflates the body before FastAPI parsing and the HMAC
check read it, so both compressed and plain uploads validate identically.
"""

from __future__ import annotations

import gzip
import json
import zlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.server._parses_ingest_fixtures import (
    _fake_require_user,
    _minimal_payload,
    _sign,
)


def _gzipped_signed_kwargs(payload: dict, token: str = "eq2c_test_token") -> dict:
    """What a v0.1.16+ plugin sends: gzip body, HMAC over the UNCOMPRESSED
    JSON (the signing contract is unchanged from plain uploads)."""
    body_bytes = json.dumps(payload).encode("utf-8")
    return {
        "content": gzip.compress(body_bytes),
        "headers": {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "X-Lexicon-Signature": _sign(body_bytes, token),
        },
    }


@pytest.mark.asyncio
async def test_gzipped_ingest_validates_hmac_and_inserts(app):
    sync_result = ("inserted", 42, 2, 1, 2)
    with (
        patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user),
        patch("backend.server.api.parses.ingest._resolve_uploader_guild_async", new=AsyncMock(return_value="Exordium")),
        patch("backend.server.api.parses.ingest._resolve_and_update_snapshots", new=AsyncMock()),
        patch("backend.server.api.parses.ingest._ingest_payload_sync", new=MagicMock(return_value=sync_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **_gzipped_signed_kwargs(_minimal_payload()))

    assert r.status_code == 201
    assert r.json()["status"] == "inserted"


@pytest.mark.asyncio
async def test_gzipped_ingest_rejects_tampered_body(app):
    """The HMAC contract survives compression: a signature over different
    JSON than what was gzipped must 401 exactly like a plain tamper."""
    kwargs = _gzipped_signed_kwargs(_minimal_payload())
    kwargs["headers"]["X-Lexicon-Signature"] = _sign(b'{"tampered": true}', "eq2c_test_token")
    with patch("backend.server.api.parses.ingest.require_user_session_or_token", _fake_require_user):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/parses/ingest", **kwargs)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_garbage_gzip_body_is_400(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/parses/ingest",
            content=b"this is not gzip at all",
            headers={"Content-Encoding": "gzip", "Content-Type": "application/json"},
        )
    assert r.status_code == 400
    assert "gzip" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_zip_bomb_is_413(app):
    # 20 MB of zeros compresses to ~20 KB but inflates past the 16 MB cap.
    bomb = gzip.compress(b"\x00" * (20 * 1024 * 1024))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/parses/ingest",
            content=bomb,
            headers={"Content-Encoding": "gzip", "Content-Type": "application/json"},
        )
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_requests_without_content_encoding_pass_through_untouched(app):
    """Pre-gzip plugins and every browser request must be completely
    unaffected — a plain (unsigned, unauthenticated) ingest POST still gets
    the normal 401, not a middleware error."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/parses/ingest", json=_minimal_payload())
    assert r.status_code == 401


def test_decompress_cap_matches_module_constant():
    from backend.server.core import gzip_request

    # The cap must comfortably exceed the plugin's own 10 MiB payload cap.
    assert gzip_request.MAX_DECOMPRESSED_BYTES >= 10 * 1024 * 1024
    # zlib gzip-container wbits sanity — decompressing a gzip stream works.
    d = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    assert d.decompress(gzip.compress(b"hello")) == b"hello"
