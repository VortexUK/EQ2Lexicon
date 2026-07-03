"""Twitch channel URL validation + content blocklist.

Used by the raid-schedule write path: an officer-supplied stream link must be a
real ``twitch.tv/<channel>`` URL (anything else is rejected), and the channel
name is screened against a profanity/blocklist. A blocklist hit is rejected AND
reported to admins via audit_log by the caller.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Twitch logins: 4–25 chars, letters/digits/underscore. Accept the bare host,
# http(s), and a leading www., with an optional trailing slash.
_TWITCH_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?twitch\.tv/([a-zA-Z0-9_]{4,25})/?$",
    re.IGNORECASE,
)

_BLOCKLIST_PATH = Path(__file__).resolve().parents[3] / "data" / "twitch_blocklist.json"


def parse_twitch_login(url: str | None) -> str | None:
    """Extract + validate the channel login from a Twitch URL.

    Returns the lower-cased login, or None if ``url`` isn't a twitch.tv channel
    URL (wrong host, path, or an out-of-spec login)."""
    if not url or not url.strip():
        return None
    # Drop any query string / fragment before matching (e.g. ?referrer=…).
    stripped = url.strip().split("?", 1)[0].split("#", 1)[0]
    m = _TWITCH_URL_RE.match(stripped)
    return m.group(1).lower() if m else None


def _load_blocklist() -> list[str]:
    """Re-read the blocklist on every call so edits take effect without a
    restart (mirrors the spell blocklist)."""
    if not _BLOCKLIST_PATH.exists():
        return []
    try:
        data = json.loads(_BLOCKLIST_PATH.read_text(encoding="utf-8"))
        return [str(w).lower() for w in data.get("blocked", []) if w]
    except Exception:
        return []


def is_blocked(login: str) -> str | None:
    """Return the matched blocklist term if the login contains disallowed
    content (case-insensitive substring), else None."""
    low = login.lower()
    for term in _load_blocklist():
        if term in low:
            return term
    return None
