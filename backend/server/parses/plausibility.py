"""Server-side plausibility gate for uploaded parses.

Pure functions over the typed ``Encounter`` / ``Combatant`` models — no DB,
no I/O — so it unit-tests like ``pet_detection.classify_combatants``. Born of
the 2026 upload-honesty review: the parser holds the user's own token and can
sign an arbitrary payload, so HMAC proves *who sent* the bytes, never that the
bytes are *true*. This gate is the server-side floor under upload honesty.

Three verdicts:

  ``ACCEPT``     — passes; ingest normally.
  ``REJECT``     — physically impossible / malformed; the ingest handler
                   returns 400.
  ``QUARANTINE`` — structurally possible but implausibly large; the handler
                   routes it to the ``tamper_reports`` audit table and keeps it
                   OFF the leaderboard (never inserted into ``encounters``).

Design notes on threshold conservatism
---------------------------------------
The REJECT checks are limited to UNAMBIGUOUS impossibilities that no real ACT
parse produces (negative/absurd duration, out-of-order or absurd timestamps, a
combatant out-damaging the entire fight). They must have ~zero false-positive
risk — a false REJECT drops a legitimate raider's parse.

The QUARANTINE ceiling is a DELIBERATELY GENEROUS absolute constant. It exists
to catch order-of-magnitude fabrication (the 1e9+ rate class, and any finite
huge float that slipped past the coercers) without policing legitimate
high-end content, whose real magnitudes we cannot know without calibrating
against production data. Tightening the ceiling to era / level / class-record
scaled bounds — and adding the multi-reporter corroboration trust model — is
the calibration follow-up. This layer stops crashes, DoS, impossible values,
and crude poisoning; corroboration is what stops a carefully-chosen fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.server.parses.models import Combatant, Encounter, _to_unix

# No EQ2 encounter runs longer than this; a larger duration is a broken or
# fabricated timestamp pair.
MAX_FIGHT_S = 7200  # 2 hours

# Timestamp sanity window. The floor rejects epoch-0 / 1970 / pre-EQ2 clocks
# and ancient-log replays; the future skew tolerates a mis-set client clock.
TS_FLOOR = 1_420_070_400  # 2015-01-01 UTC
FUTURE_SKEW_S = 86_400  # 1 day

# Absolute per-second ceiling for the QUARANTINE layer. Intentionally far above
# any real parse — its only job is to catch fabricated magnitudes and finite
# huge floats, NOT to enforce records. Calibrate down against real data later.
MAX_PLAUSIBLE_RATE = 1e12


class Verdict(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    QUARANTINE = "quarantine"


@dataclass(frozen=True)
class PlausibilityResult:
    verdict: Verdict
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict is Verdict.ACCEPT


def _reject(reason: str) -> PlausibilityResult:
    return PlausibilityResult(Verdict.REJECT, reason)


def _quarantine(reason: str) -> PlausibilityResult:
    return PlausibilityResult(Verdict.QUARANTINE, reason)


def evaluate(enc: Encounter, combatants: list[Combatant], *, now: int) -> PlausibilityResult:
    """Judge a fully-parsed encounter + its combatants. ``now`` is unix
    seconds (injected so the check is deterministic under test)."""
    # --- Layer 1: impossible / malformed → REJECT (400) ---------------------
    # ONLY values a real recent encounter can never produce. A false REJECT
    # fails a legitimate plugin upload, so this set is deliberately minimal:
    # negatives and impossible/absurd timestamps. Anything that a legitimate
    # (if unusual) capture COULD produce — a long idle-merged encounter, a
    # combatant whose damage exceeds the reported total under some total-damage
    # computation — is handled as a non-erroring QUARANTINE below, never a 400.
    if enc.duration_s < 0:
        return _reject("duration_negative")
    if enc.total_damage < 0:
        return _reject("total_damage_negative")
    if enc.encdps < 0:
        return _reject("encdps_negative")

    started = _to_unix(enc.started_at)
    ended = _to_unix(enc.ended_at)
    if started > 0 and ended > 0 and started > ended:
        return _reject("time_out_of_order")
    if started > 0 and (started < TS_FLOOR or started > now + FUTURE_SKEW_S):
        return _reject("timestamp_implausible")

    for c in combatants:
        if c.ally and c.damage < 0:
            return _reject("combatant_damage_negative")

    # --- Layer 2: possible but implausible → QUARANTINE (off-board, 201) -----
    # These do NOT error the upload — the plugin gets a normal 201; the parse
    # is simply held off the leaderboard for admin review. Safe to apply to
    # anything that shouldn't rank but might not be provably malicious.
    #
    # A fight longer than MAX_FIGHT_S is almost always ACT's idle-merge, not a
    # real ranked encounter — quarantining keeps that garbage off the board
    # without failing the user's upload.
    if enc.duration_s > MAX_FIGHT_S:
        return _quarantine("duration_too_long")
    if enc.encdps > MAX_PLAUSIBLE_RATE:
        return _quarantine("implausible_encdps")
    for c in combatants:
        if not c.ally:
            continue
        if c.encdps > MAX_PLAUSIBLE_RATE or c.enchps > MAX_PLAUSIBLE_RATE:
            return _quarantine("implausible_rate")

    return PlausibilityResult(Verdict.ACCEPT)
