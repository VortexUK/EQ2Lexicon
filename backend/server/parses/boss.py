"""Authoritative server-side boss detection.

EQ2 trash mobs are named with a lowercase article ("a krait warrior",
"an ancient guard"); bosses have a proper capitalised name. First-character
uppercase is the simplest reliable signal. The frontend keeps a matching copy
in ParsesPage.tsx; this server version is authoritative for rankings + deletes.
"""

from __future__ import annotations

import unicodedata


def is_boss(title: str | None) -> bool:
    return bool(title) and "A" <= title[0] <= "Z"


# Mirror of frontend normaliseBossName in RankingsPage.tsx — keep in sync.
# Folds the full set of apostrophe-like and space-like Unicode codepoints
# seen in ACT logs and curator-entered roster data so a boss-index lookup or
# a fight key can't silently miss on a codepoint mismatch.
_APOSTROPHE_VARIANTS = str.maketrans(
    {
        "`": "'",  # U+0060 GRAVE ACCENT
        "´": "'",  # U+00B4 ACUTE ACCENT
        "ʹ": "'",  # U+02B9 MODIFIER LETTER PRIME
        "ʺ": "'",  # U+02BA MODIFIER LETTER DOUBLE PRIME
        "ʻ": "'",  # U+02BB MODIFIER LETTER TURNED COMMA
        "ʼ": "'",  # U+02BC MODIFIER LETTER APOSTROPHE
        "ʽ": "'",  # U+02BD MODIFIER LETTER REVERSED COMMA
        "ʾ": "'",  # U+02BE MODIFIER LETTER RIGHT HALF RING
        "ʿ": "'",  # U+02BF MODIFIER LETTER LEFT HALF RING
        "ˈ": "'",  # U+02C8 MODIFIER LETTER VERTICAL LINE
        "‘": "'",  # U+2018 LEFT SINGLE QUOTATION MARK
        "’": "'",  # U+2019 RIGHT SINGLE QUOTATION MARK
        "‛": "'",  # U+201B SINGLE HIGH-REVERSED-9 QUOTATION MARK
        "′": "'",  # U+2032 PRIME
        "＇": "'",  # U+FF07 FULLWIDTH APOSTROPHE
        " ": " ",  # U+00A0 NO-BREAK SPACE
        " ": " ",  # U+2009 THIN SPACE
        " ": " ",  # U+200A HAIR SPACE
        " ": " ",  # U+202F NARROW NO-BREAK SPACE
        " ": " ",  # U+205F MEDIUM MATHEMATICAL SPACE
        "　": " ",  # U+3000 IDEOGRAPHIC SPACE
    }
)


def boss_key(title: str) -> str:
    """Lowercase + Unicode NFC + collapse apostrophe/space variants: the one
    comparison key for mob names, shared by the rankings boss index, the
    raid-boss pack and fight grouping. Frontend mirror: normaliseBossName in
    RankingsPage.tsx."""
    return unicodedata.normalize("NFC", title).lower().translate(_APOSTROPHE_VARIANTS).strip()
