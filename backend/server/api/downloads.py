"""Download counters for the Downloads page.

Each direct-download link (parser installer / portable zip / ACT plugin dll)
records a click keyed by (user, slug), so the count is distinct-downloaders.
The whole app is behind the login gate, so every click carries a session.

The frontend only *surfaces* a count once it clears a small threshold (so a
brand-new download doesn't advertise "3 downloads"); this API always returns
the true numbers for every allowlisted slug.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.core.log_safety import scrub as _scrub
from backend.server.auth_deps import require_user_session
from backend.server.db.downloads import store as downloads_db
from backend.server.limiter import limiter

_log = logging.getLogger(__name__)

router = APIRouter(tags=["downloads"])

#: The only slugs the record endpoint accepts — a client can't spam arbitrary
#: keys into the table. Keep in sync with the buttons in DownloadsPage.tsx.
DOWNLOAD_SLUGS = frozenset({"parser-setup", "parser-portable", "act-plugin"})


class DownloadCounts(BaseModel):
    #: slug → distinct-downloader count; always carries every allowlisted slug.
    counts: dict[str, int]


async def _all_counts() -> DownloadCounts:
    raw = await downloads_db.counts()
    return DownloadCounts(counts={slug: raw.get(slug, 0) for slug in DOWNLOAD_SLUGS})


@router.get("/downloads/counts", response_model=DownloadCounts)
@limiter.limit("60/minute")
async def get_download_counts(request: Request) -> DownloadCounts:
    """Distinct-downloader counts per slug (an untouched slug reports 0)."""
    return await _all_counts()


@router.post("/downloads/{slug}", response_model=DownloadCounts)
@limiter.limit("30/minute")
async def record_download(request: Request, slug: str) -> DownloadCounts:
    """Record that the current user clicked a download link, then return the
    fresh counts. Idempotent per (user, slug)."""
    if slug not in DOWNLOAD_SLUGS:
        raise HTTPException(status_code=404, detail="Unknown download.")
    user = require_user_session(request)
    if await downloads_db.record_download(user["id"], slug):
        _log.info("[downloads] %s downloaded %s", _scrub(user["id"]), slug)
    return await _all_counts()
