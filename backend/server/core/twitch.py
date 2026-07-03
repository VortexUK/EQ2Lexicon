"""Twitch channel URL validation.

Used by the raid-schedule write path: an officer-supplied stream link must be a
real ``twitch.tv/<channel>`` URL (anything else is rejected), and the channel
name is screened against the shared word blocklist. A blocklist hit is rejected
AND reported to admins via audit_log by the caller.
"""

from __future__ import annotations

import re

from backend.server.core.text_moderation import contains_blocked_term

# Twitch logins: 4–25 chars, letters/digits/underscore. Accept the bare host,
# http(s), and a leading www., with an optional trailing slash.
_TWITCH_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?twitch\.tv/([a-zA-Z0-9_]{4,25})/?$",
    re.IGNORECASE,
)


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


def is_blocked(login: str) -> str | None:
    """Return the matched blocklist term if the login contains disallowed
    content, else None. Thin wrapper over the shared text-moderation screen."""
    return contains_blocked_term(login)
