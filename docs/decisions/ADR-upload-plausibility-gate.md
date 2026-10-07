# ADR: Server-side plausibility gate for parse uploads

Status: accepted, implemented in `backend/server/parses/plausibility.py`,
called from `backend/server/api/parses/ingest.py`.

## Context

Parse uploads feed public leaderboards. The ACT plugin signs each payload with
an HMAC keyed by the uploader's own API token, which proves *who sent* the
bytes and that they were not altered in flight. It does not prove the bytes
are *true*: the token holder has the key and can sign any JSON they like. A
client bug can also send nonsense (negative values, inverted timestamps,
non-finite floats) that would crash the insert or poison leaderboard maths.

The server has no ground truth for what a legitimate parse looks like at the
high end. Real magnitudes vary by era, level, class and gear, and there is not
yet enough production data to calibrate tight per-context bounds. A check that
is too strict silently drops a real raider's best parse, which is worse than
letting a borderline one through.

## Decision

Every upload passes through a pure function, `evaluate(encounter, combatants,
now)`, before any Census or database work. It returns one of three verdicts:

- **ACCEPT** — ingest normally.
- **REJECT** — the handler returns 400. Reserved for values no real capture can
  produce: negative duration, damage or DPS; start after end; timestamps before
  2015 or more than a day in the future; a duration longer than the window its
  own timestamps describe (plus 5 s rounding slack); an ally with negative
  damage; any percentage outside 0-100. These must carry essentially zero
  false-positive risk.
- **QUARANTINE** — structurally possible but implausible. The payload is stored
  in `tamper_reports` (reason prefixed `server_`) for admin review, never
  inserted into `encounters`, and the client receives a normal-looking 201 with
  `status="quarantined"`. Triggers: a fight longer than two hours (almost
  always ACT's idle merge), any rate above `MAX_PLAUSIBLE_RATE` (1e12), and an
  ally whose damage exceeds the fight total by more than 5 percent (the shape of
  a forged row; the slack covers ACT's differing total computations).

Anything a legitimate but unusual capture could produce goes to QUARANTINE, not
REJECT, so it costs an admin a look rather than costing the raider their parse.

The QUARANTINE rate ceiling is deliberately far above any real parse. Its job
is to catch order-of-magnitude fabrication and finite huge floats that slipped
past the coercion helpers, not to police records.

Supporting rules elsewhere:

- The coercion helpers in `backend/server/parses/models.py` clamp integers to
  the Postgres bigint range and turn non-finite floats into 0, so garbage
  becomes a value this gate can judge instead of an insert-time crash.
  Percentages are deliberately *not* clamped, so a forged "9999%" is rejected
  rather than silently masked to 100.

## Consequences

- Crashes, impossible values, denial-of-service-sized numbers and crude
  leaderboard poisoning are stopped cheaply, before any expensive work.
- A carefully chosen fake that stays within plausible bounds still gets
  through. The planned follow-ups are tighter bounds scaled by era, level and
  class record once there is production data to calibrate against, and a
  multi-reporter corroboration model (a fight attested by several independent
  raiders' mirrors is more trustworthy than a lone upload).
- Quarantine gives a forger no feedback to tune against, but it also means a
  legitimate upload caught by the ceiling disappears from the board silently
  until an admin reviews the tamper-report queue. Keep the ceiling generous.
