"""Pet detection pipeline for parses.

Replaces the legacy `ally=1 AND single-word AND name != 'Unknown'` SQL
heuristic with a 6-stage classifier that better separates real players
from EQ2 auto-named pets, multi-word pet names, and unresolved-by-Census
combatants. The output is persisted as `combatants.is_player` and is the
authoritative signal used by every reader (parses list, individual parse
detail, rankings scope, Phase 4 merger top-N).

The classifier is a pure function — no DB access, no side effects.
Callers fetch the combatant rows + the zone category and the helpers in
``parses/db.py`` (``update_combatant_is_player`` etc.) persist the result.

See docs/superpowers/specs/2026-05-30-pet-detection-pipeline-design.md
for the full design rationale and the bucket-fill rule table.
"""

from __future__ import annotations

import re
from typing import Literal

# ── Stage 4: EQ2 auto-named-pet regex ────────────────────────────────────
# Matches the typical [gkjxzv][ieaov]… stem + optional middle syllable +
# common ending that EQ2's pet-naming code produces (Gibab, Zosn, Kebn,
# Zebekn, Jentik). The regex was prototyped via scripts/dev/pet_name_detector.py
# against an observed sample of pet names in parses.
EQ2_PET_PATTERN = re.compile(
    r"""
    ^
    (?:[gkjxzv]i|[gkjxzv]e|[gkjxzv]a|[gkjxzv]o|je|jo|ja|ze|zo|ke|gi)
    (?:ba|be|bo|na|ne|ti|an)?
    (?:b|bn|ber|bekn|btik|tik|ntik|ner|sn|kn|n)
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Safety-net for names the regex misses. Add observed-in-the-wild auto-pet
# names that fall outside the pattern but are clearly pets.
KNOWN_EXAMPLES = {
    "gibab",
    "zosn",
    "kebn",
    "zebekn",
    "jentik",
}


# ── Pipeline ────────────────────────────────────────────────────────────


def _is_known_pet_name(name: str) -> bool:
    """Stage 4 helper: regex match OR explicit known-example."""
    lower = name.strip().lower()
    if lower in KNOWN_EXAMPLES:
        return True
    return bool(EQ2_PET_PATTERN.match(lower))


def classify_combatants(
    combatants: list[dict],
    zone_category: Literal["raid", "dungeon", "other"],
) -> dict[int, bool]:
    """Return {combatant.id: is_player} for every ally combatant.

    Enemies (ally != 1) are omitted from the result entirely — they're
    irrelevant to the player_count question. The dict keys are combatant
    `id` values, values are True (player) or False (pet/NPC/unresolved).

    Pipeline:
      1. ally != 1 → omit
      2. name in {"", "Unknown"} → pet
      3. " " in name → pet (multi-word)
      4. EQ2_PET_PATTERN.match(name) or name in KNOWN_EXAMPLES → pet
      5. cls is truthy → player (Census-resolved at ingest or async fill)
      6. survived 1-5 → unconfirmed. Bucket-fill applies (see below).

    Bucket-fill (per the spec's rule table):

      raid    : fill confirmed up to 24, then trim if final > 24
      dungeon : fill confirmed up to 6 (additive only — no trim)
      other   : if n_total ≤ 6  → no-op
                if n_total 7-10  → fill confirmed up to 6
                if n_total ≥ 11  → treat as raid (fill to 24, trim if > 24)

    Promotion order within bucket-fill: unconfirmed allies sorted by
    (encdps + enchps) DESC with name ASC tiebreaker; promote until target
    reached or pool exhausted. Demotion (trim) order: confirmed allies
    sorted by (encdps + enchps) ASC with name ASC tiebreaker; demote
    lowest contributors back to pet until target reached.

    Defensive: missing keys (`ally`, `name`, `cls`, `encdps`, `enchps`)
    are treated as absent; the function never raises on malformed input.
    """
    # Stage 1: keep only allies.
    allies = [c for c in combatants if c.get("ally") == 1]
    if not allies:
        return {}

    # Stages 2-5: classify each ally into pet / player / unconfirmed.
    pets: list[dict] = []
    players: list[dict] = []
    unconfirmed: list[dict] = []
    for c in allies:
        name = (c.get("name") or "").strip()
        if name == "" or name == "Unknown":
            pets.append(c)
            continue
        if " " in name:
            pets.append(c)
            continue
        if _is_known_pet_name(name):
            pets.append(c)
            continue
        if c.get("cls"):
            players.append(c)
            continue
        unconfirmed.append(c)

    n_total = len(allies)
    n_player = len(players)

    # Stage 6: bucket-fill target by zone category + total ally count.
    target: int | None
    if zone_category == "raid":
        target = 24
    elif zone_category == "dungeon":
        target = 6
    elif zone_category == "other":
        if n_total <= 6:
            target = None  # no-op
        elif n_total <= 10:
            target = 6
        else:
            target = 24
    else:  # unknown category — treat conservatively (no fill)
        target = None

    if target is not None:
        # Fill: promote highest-contributing unconfirmed up to target.
        if n_player < target and unconfirmed:
            unconfirmed.sort(
                key=lambda c: (-(float(c.get("encdps") or 0) + float(c.get("enchps") or 0)), c.get("name") or ""),
            )
            promote_count = min(target - n_player, len(unconfirmed))
            promoted = unconfirmed[:promote_count]
            remaining_unconfirmed = unconfirmed[promote_count:]
            players.extend(promoted)
            pets.extend(remaining_unconfirmed)
            n_player += promote_count
        else:
            pets.extend(unconfirmed)

        # Trim: cap confirmed-player count at target IF currently above.
        # Only the raid / raid-treated paths actually allow this branch —
        # dungeon and other-≤-10 targets never set n_player > target via
        # this function, but a parse can arrive with 25+ Census-confirmed
        # allies (mercs/swap-ins) and needs trimming to honour the 24 cap.
        if n_player > target and zone_category in ("raid", "other"):
            players.sort(
                key=lambda c: (float(c.get("encdps") or 0) + float(c.get("enchps") or 0), c.get("name") or ""),
            )
            demote_count = n_player - target
            demoted = players[:demote_count]
            players = players[demote_count:]
            pets.extend(demoted)
            n_player = target
    else:
        # No bucket-fill — unconfirmed stay pets.
        pets.extend(unconfirmed)

    out: dict[int, bool] = {}
    for c in players:
        out[int(c["id"])] = True
    for c in pets:
        out[int(c["id"])] = False
    return out
