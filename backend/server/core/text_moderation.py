"""Shared text moderation: a profanity screen + input sanitisation.

Backs both the Twitch-login check and the raid-team free-text fields (team name,
raid label) on the raid-schedule save path. The wordlist comes from the
maintained ``better-profanity`` package (which also generates leetspeak variants)
so we don't hand-curate a slur list in the repo.

Before screening we normalise: NFKC, strip invisible/bidi/control chars (which
would otherwise split a word and defeat the match), and collapse whitespace.
Matching is word-based, so it won't fire on an innocent superstring (e.g.
"Grapevine") — the trade-off is it also won't catch a slur buried inside a single
token or spaced out letter-by-letter. Acceptable for a best-effort, officer-gated
field: the audit trail + admin reach remain the real backstop.
"""

from __future__ import annotations

import re
import unicodedata

from better_profanity import profanity

# Load the default wordlist (+ generated leetspeak variants) once at import.
profanity.load_censor_words()

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _strip_control(text: str) -> str:
    """Drop Unicode control/format chars (Cc/Cf/Cs/Co/Cn) — zero-width joiners,
    bidi overrides, ASCII control bytes used to break up or spoof text. Ordinary
    whitespace (space/tab/newline) is kept so it still acts as a word separator;
    zero-width chars aren't ``isspace()`` so they're removed."""
    return "".join(ch for ch in text if ch.isspace() or not unicodedata.category(ch).startswith("C"))


def _normalize(text: str) -> str:
    """NFKC-normalise, strip invisible/control chars, collapse whitespace, trim."""
    t = unicodedata.normalize("NFKC", text)
    t = _strip_control(t)
    return re.sub(r"\s+", " ", t).strip()


def contains_blocked_term(text: str | None) -> str | None:
    """Return an identifier for the profanity if the text is disallowed, else None.

    The returned value is the offending token (for the audit-log ``reason``), or
    the generic ``"profanity"`` if the hit spans a multi-word phrase."""
    if not text:
        return None
    normalized = _normalize(text)
    if not normalized or not profanity.contains_profanity(normalized):
        return None
    for word in _WORD_RE.findall(normalized):
        if profanity.contains_profanity(word):
            return word.lower()
    return "profanity"


def sanitize_text(text: str | None, *, max_len: int) -> str:
    """Clean a free-text field for storage/display: NFKC-normalise, strip
    invisible/bidi + control chars, collapse internal whitespace, trim, and cap
    length. Does NOT screen for profanity — call ``contains_blocked_term`` too.
    """
    if not text:
        return ""
    return _normalize(text)[:max_len]
