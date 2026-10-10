"""Named constants for magic numbers scattered across the backend.

Owns: cache TTLs, refresh throttles, mirror/dedup windows, request-list caps.
Each constant carries a comment naming
the code path it gates so a future contributor can search by intent rather
than by literal value.

Adding a new constant: append here, then `from backend.server.constants import FOO`
at the consumer site. Never re-declare a constant for "local" use — the
audit found three independent `_THROTTLE = 900` / `STALE_S = 900` / `> 900`
literals for the same concept; this module exists to make that mistake
visible.
"""

from __future__ import annotations

import datetime as _dt
import os

# --- Cache TTLs ------------------------------------------------------------

# stale-while-revalidate window for character/guild/aa caches.
# Below this age → return directly; above → return + fire background refresh.
CACHE_STALE_TTL_S: int = 300  # 5 min

# Hard-expiry window — entries older than this are evicted and the next
# request MUST do a sync fetch. Bounds memory growth for never-revisited
# keys (see backend/server/cache.TTLCache.sweep).
CACHE_MAX_AGE_S: int = 3600  # 1 hr


# --- Census refresh orchestration -----------------------------------------

# Per-entity throttle: subsequent refresh attempts before this elapses are
# silently dropped. Stops a hot-cache miss from triggering hundreds of
# in-flight Census calls for the same character. Same value as the
# character-row staleness window so a stale row triggers exactly one refresh.
CENSUS_REFRESH_THROTTLE_S: int = 900  # 15 min

# A character record in census_store is "stale" once last_resolved_at is
# older than this. Surfaced on CharacterResponse.stale so the frontend can
# render a small "may be outdated" badge.
CHARACTER_STALE_S: int = 900  # 15 min


# --- Parses listing + mirroring -------------------------------------------

# Mirror grouping: two uploads are the same fight when their (guild, title)
# match and their start times fall within this window. Faithful to the
# pre-server-side ParsesPage detectMirrors rule.
PARSE_MIRROR_WINDOW_S: int = 60

# Maximum FIGHT cap on /api/parses?limit=... — protects the browser from
# stalling on a multi-thousand-row render rather than the server. The
# inner SQL cap is `limit * PARSE_INNER_CAP_MULTIPLIER` (see below).
PARSE_LIST_MAX_LIMIT: int = 500

# DELETE /api/parses/batch per-request id cap. 200 ids × ~8 chars ≈ 1.6 KB of
# query string (under the ~2 KB URL comfort limit) and 6,000 ids/min at the
# route's 30/min limit — a 500-fight /parses page with mirrors clears in a
# handful of requests. Mirrored by PARSE_BATCH_CHUNK_SIZE in
# frontend/src/pages/parses/api.ts.
PARSE_BATCH_MAX_IDS: int = 200

# Inner SQL cap multiplier — worst-case 24 mirror uploads per fight, so a
# 500-fight request needs 12_000 raw upload rows; round up to 15_000 for
# headroom. Floor of 2000 covers very small page requests.
PARSE_INNER_CAP_MULTIPLIER: int = 30
PARSE_INNER_CAP_FLOOR: int = 2000

# Admin parses listing cap — looser than the public one (an admin reviewing
# uploads needs a wider view than a casual reader).
ADMIN_PARSE_LIST_MAX_LIMIT: int = 1000

# Census guild rank_ids that count as "officer" on the site (0 = leader,
# 1 = the first officer rank). Shared by the web officer checks and the
# bot's /lexicon link gate so the two can never disagree.
OFFICER_RANK_IDS: frozenset[int] = frozenset({0, 1})


# --- API tokens -----------------------------------------------------------

# Per-token last_used_at coalescing window. UPDATE only fires if the
# existing value is older than this — sub-minute precision isn't useful to
# the UI, and it avoids a write per request during a raid upload burst.
API_TOKEN_LAST_USED_COALESCE_S: int = 60


# --- Background tasks -----------------------------------------------------

# Cache-sweep loop interval (see backend/server/app.py:_cache_sweep_loop).
CACHE_SWEEP_INTERVAL_S: int = 600  # 10 min

# Parses retention-sweep loop interval (see app.py:_parse_cleanup_loop).
# 6 h is plenty — the cutoff is days, so cadence only bounds how stale a
# just-past-cutoff parse can be before it's swept.
PARSE_CLEANUP_INTERVAL_S: int = 6 * 60 * 60  # 6 h

# Voice-channel attendance observations carry the DISCORD IDS of everyone in
# the raid voice channel — including people who never used the site. They
# only ever feed the "in voice, not in game" flag on a session, so keep them
# 90 days (the privacy policy states this) and sweep them with the parse
# retention loop.
VOICE_OBSERVATION_RETENTION_DAYS: int = 90

# Days of per-guild daily history (the census schema ``guild_history``) kept for the
# guild page's History tab. One row per guild per UTC day, written by the
# 15-minute guild refresh; the 1-year range pill needs 365, 400 leaves slack.
# Pruned on write, scoped to the guild being refreshed.
GUILD_HISTORY_RETENTION_DAYS: int = 400

# ── Spell audit (backend/server/spell_audit.py) ────────────────────────────
# A 2026-10 TLE event bug let out-of-era spell tiers drop and scribe. Any
# character carrying one is barred from the boards: every parse since the
# cutoff they played in is hidden and reported. Master is the era's ceiling;
# Grandmaster is a legitimate pick (one per 10 levels), so it stays allowed.
# Both knobs are env-overridable: SPELL_AUDIT_SINCE (ISO date/datetime, UTC
# when no offset is given) and SPELL_AUDIT_TIERS (comma-separated tier names).
SPELL_AUDIT_SINCE_TS: int = int(
    # The event went live at 00:01 Pacific on 2026-10-09 = 07:01 UTC.
    _dt.datetime.fromisoformat(os.getenv("SPELL_AUDIT_SINCE", "2026-10-09T07:01:00+00:00"))
    .replace(tzinfo=_dt.UTC)
    .timestamp()
)
# When the embargo is lifted, set SPELL_AUDIT_UNTIL (ISO datetime, UTC):
# parses started after it rank again; everything started inside the window
# keeps its permanent ranking_barred_at stamp. Unset = the window is open.
_until = os.getenv("SPELL_AUDIT_UNTIL", "").strip()
SPELL_AUDIT_UNTIL_TS: int | None = (
    int(_dt.datetime.fromisoformat(_until).replace(tzinfo=_dt.UTC).timestamp()) if _until else None
)
# The whole filter is one switch. SPELL_AUDIT_ENABLED=0 stops stamping and
# sweeping, and at startup removes every bar this feature placed, so all
# parses rank again. Nothing the filter does is destructive.
SPELL_AUDIT_ENABLED: bool = os.getenv("SPELL_AUDIT_ENABLED", "1").strip().lower() not in ("0", "false", "no")
SPELL_AUDIT_DISALLOWED_TIERS: tuple[str, ...] = tuple(
    t.strip() for t in os.getenv("SPELL_AUDIT_TIERS", "Ancient,Celestial").split(",") if t.strip()
)
