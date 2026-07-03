"""Shared text moderation: a profanity/blocklist screen + input sanitisation.

Backs both the Twitch-login check and the raid-team free-text fields (team name,
raid label) on the raid-schedule save path. One blocklist file
(``data/word_blocklist.json``), re-read on every check so a curator edit takes
effect without a restart (mirrors the spell blocklist).

The matcher is deliberately aggressive about evasion: it normalises the input
(NFKC, lower-case, strips invisible/bidi chars, folds common leetspeak, drops
non-letters) before a substring test, so "P0rn Guy" and "f a g g o t" are caught.
The trade-off is that a blocklist term which is a substring of an innocent word
can fire a false positive — acceptable for a best-effort, officer-gated field
(a false hit just asks the officer to pick another name). Curators should avoid
adding terms that are common English substrings.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

_BLOCKLIST_PATH = Path(__file__).resolve().parents[3] / "data" / "word_blocklist.json"

# Common leetspeak digit/symbol -> letter folds, applied before matching so
# "p0rn" / "f4ggot" / "n1gger" collapse to their plain form.
_LEET = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s", "@": "a", "!": "i", "|": "i"}
)


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


def _strip_control(text: str) -> str:
    """Drop Unicode control/format chars (Cc/Cf/Cs/Co/Cn) — zero-width joiners,
    bidi overrides, ASCII control bytes used to break up or spoof text. Ordinary
    whitespace (space/tab/newline) is kept so it still acts as a word separator;
    zero-width chars aren't ``isspace()`` so they're removed."""
    return "".join(ch for ch in text if ch.isspace() or not unicodedata.category(ch).startswith("C"))


def _squash(text: str) -> str:
    """Reduce text to a bare-letter comparison form: NFKC, lower-case, strip
    control/invisible chars, fold leetspeak, then drop everything that isn't a
    letter. "n i g g e r", "n.i.g.g.e.r" and "p0rn" all collapse to plain
    letters so spacing/punctuation/digit evasion fails."""
    t = unicodedata.normalize("NFKC", text).lower()
    t = _strip_control(t)
    t = t.translate(_LEET)
    return re.sub(r"[^a-z]", "", t)


def contains_blocked_term(text: str | None) -> str | None:
    """Return the first blocklist term the normalised text contains, else None."""
    if not text:
        return None
    squashed = _squash(text)
    if not squashed:
        return None
    for term in _load_blocklist():
        needle = _squash(term)
        if needle and needle in squashed:
            return term
    return None


def sanitize_text(text: str | None, *, max_len: int) -> str:
    """Clean a free-text field for storage/display: NFKC-normalise, strip
    invisible/bidi + control chars, collapse internal whitespace, trim, and cap
    length. Does NOT screen for profanity — call ``contains_blocked_term`` too.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = _strip_control(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_len]
