"""Strip CR/LF from values before they hit a log line.

Without this, a hostile user-controlled value (a character name, a guild
name, a header value) could inject forged log lines via embedded CR/LF
sequences — CWE-117 log injection. The fix is mechanical: stringify, then
strip the two characters that delimit log records.

Replaces three duplicated variants in census_refresh.py / claim.py / guild.py
(and the inline one in parses.py).
"""

from __future__ import annotations


def scrub(value: object) -> str:
    """Return ``str(value)`` with CR and LF replaced by spaces.

    Use this everywhere a user-supplied value (character name, guild name,
    Authorization header, etc.) is about to be interpolated into a log line.
    A no-op for already-safe values, so the cost of using it defensively is
    negligible.
    """
    return str(value).replace("\r", " ").replace("\n", " ")
