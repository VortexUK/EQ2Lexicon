"""Regression test for the 2026-05-31 SPA stale-cache hotfix.

After a deploy with a new Vite build, browsers that cached the old
``index.html`` request hashed chunk URLs that no longer exist. Without
proper Cache-Control headers, browsers can hold stale index.html for
hours and the user gets a blank page until they hard-refresh.

The fix:
  * ``index.html`` is served with ``Cache-Control: no-cache, must-revalidate``
    so the browser always re-validates on navigation.
  * Hashed ``/assets/*`` files are served with
    ``Cache-Control: public, max-age=31536000, immutable``
    (Vite's content hashes make them safe to cache forever).

These tests pin the contract so a future refactor of the static-serving
layer doesn't regress it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app_with_built_frontend():
    """Spin up the FastAPI app with a fake frontend/dist directory populated
    with a minimal index.html + one hashed asset, so the SPA fallback +
    /assets mount both have something to serve."""
    with tempfile.TemporaryDirectory() as tmp:
        dist = Path(tmp) / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text(
            "<!doctype html><html><head>"
            "<title>EQ2 Lexicon</title>"
            '<meta property="og:image" content="https://eq2lexicon.com/og-image.png" />'
            '<meta property="og:image:width" content="1536" />'
            '<meta property="og:image:height" content="1024" />'
            '<meta name="twitter:card" content="summary_large_image" />'
            '<meta name="twitter:image" content="https://eq2lexicon.com/og-image.png" />'
            "</head><body>fake spa</body></html>"
        )
        (dist / "assets" / "main-abc123.js").write_text("console.log('fake bundle');")
        (dist / "favicon.svg").write_text("<svg/>")

        # Patch the FRONTEND_DIST constant used by create_app() so the
        # mount + serve_spa pick up our fake directory.
        with patch("backend.server.app._FRONTEND_DIST", dist):
            from backend.server.app import create_app

            yield create_app()


@pytest.mark.asyncio
async def test_index_html_has_no_cache_headers(app_with_built_frontend):
    """The root path falls back to index.html. It MUST be served with
    no-cache so a deploy's new chunk hashes are picked up on the next
    navigation."""
    async with AsyncClient(transport=ASGITransport(app=app_with_built_frontend), base_url="http://test") as client:
        r = await client.get("/")
    assert r.status_code == 200, r.text
    cc = r.headers.get("cache-control", "")
    assert "no-cache" in cc.lower(), f"index.html must have no-cache; got {cc!r}"


@pytest.mark.asyncio
async def test_spa_fallback_path_has_no_cache_headers(app_with_built_frontend):
    """Any in-app route (e.g. /raids) also falls back to index.html and
    needs the same no-cache treatment — they all serve the same HTML."""
    async with AsyncClient(transport=ASGITransport(app=app_with_built_frontend), base_url="http://test") as client:
        r = await client.get("/raids")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "no-cache" in cc.lower(), f"SPA route must have no-cache; got {cc!r}"


@pytest.mark.asyncio
async def test_hashed_asset_has_immutable_cache(app_with_built_frontend):
    """Hashed chunks under /assets are content-addressed by Vite —
    browsers can safely cache them forever (the hash changes on any
    content change, so cache is automatically busted)."""
    async with AsyncClient(transport=ASGITransport(app=app_with_built_frontend), base_url="http://test") as client:
        r = await client.get("/assets/main-abc123.js")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "max-age=31536000" in cc, f"hashed asset must be cache-forever; got {cc!r}"
    assert "immutable" in cc, f"hashed asset must be immutable; got {cc!r}"


@pytest.mark.asyncio
async def test_root_serves_hero_embed(app_with_built_frontend):
    """The landing page keeps the big hero embed (og-image.png + large card)."""
    async with AsyncClient(transport=ASGITransport(app=app_with_built_frontend), base_url="http://test") as client:
        r = await client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "og-image.png" in body
    assert "summary_large_image" in body
    assert "favicon-192.png" not in body


@pytest.mark.asyncio
async def test_deep_link_serves_compact_embed(app_with_built_frontend):
    """A deep link (item/character/etc.) swaps to the small square logo + the
    compact `summary` card so Discord shows a thumbnail, not the hero."""
    async with AsyncClient(transport=ASGITransport(app=app_with_built_frontend), base_url="http://test") as client:
        r = await client.get("/item/12345")
    assert r.status_code == 200
    body = r.text
    assert "favicon-192.png" in body
    assert 'content="summary"' in body
    # The hero variants must be gone.
    assert "og-image.png" not in body
    assert "summary_large_image" not in body
    # Compact card uses a square image.
    assert 'content="192"' in body


@pytest.mark.asyncio
async def test_root_non_hashed_static_file_has_no_aggressive_cache(app_with_built_frontend):
    """Root-level static files like favicon.svg are NOT content-hashed,
    so they should NOT be cached forever — they need similar
    no-cache semantics so a logo swap propagates immediately. Loose
    assertion: any cache header is fine as long as it isn't the
    forever-cache reserved for hashed assets."""
    async with AsyncClient(transport=ASGITransport(app=app_with_built_frontend), base_url="http://test") as client:
        r = await client.get("/favicon.svg")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "max-age=31536000" not in cc, f"non-hashed root file must not be cached forever; got {cc!r}"
