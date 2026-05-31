"""POST /parses/tamper-report — audit channel for client-detected tamper.

Companion to /parses/ingest. When the ACT plugin's heuristics decide a
parse is tampered (renamed encounter, stale import, recent import activity)
it blocks the leaderboard upload and POSTs the same payload here instead
with an ``X-Lexicon-Tamper-Reason`` header. We persist the row in the
``tamper_reports`` table for admin review and respond 201.

Crucially this endpoint NEVER writes to ``encounters`` — the parse must
not appear on public leaderboards. Admins read these rows via
``GET /api/admin/tamper-reports`` (see web/routes/admin.py).

Plugin contract (mirror CLAUDE.md "/api/parses/tamper-report (POST)"):
  * Headers: Authorization Bearer, X-Lexicon-Tamper-Reason (one code),
    X-Lexicon-Signature (HMAC matches ingest scheme)
  * Body shape: identical to IngestRequest (so admins can drill in with
    the same parse-row reader the rest of the site uses)
  * Reason codes today: title_enemy_mismatch | stale_encounter |
    recent_import_activity. Stored as TEXT so future codes need no
    server change.

The plugin is fire-and-forget here — it never surfaces the response to
the user, even on failure. So we keep the response minimal (id + reason)
and rely on logging for diagnostics.
"""

from __future__ import annotations

import logging
import sqlite3
import time

from fastapi import HTTPException, Request

from parses import db as parses_db
from web.auth_deps import require_user_session_or_token
from web.lib.executor import run_sync
from web.lib.session_user import TokenUser
from web.lib.validation import sanitize_world as _sanitize_world
from web.lib.validation import validate_character_name as _validate_character_name
from web.limiter import limiter
from web.routes.parses import router
from web.routes.parses.ingest import _validate_payload_signature
from web.routes.parses.models import IngestRequest, TamperReportResponse

_log = logging.getLogger(__name__)

# Mirror the plugin-side header name. Changing one side without the other
# silently breaks the audit channel. See
# UploadClient.TamperReasonHeaderName in the EQ2LexiconACTPlugin repo.
PLUGIN_TAMPER_REASON_HEADER = "X-Lexicon-Tamper-Reason"

# Reason codes the plugin currently emits. Stored verbatim — this set is
# for logging + admin-UI styling only. Future codes pass through unknown.
KNOWN_TAMPER_REASONS = frozenset(
    {
        "title_enemy_mismatch",
        "stale_encounter",
        "recent_import_activity",
    }
)

# Cap matches the plugin-side sanitisation (200 chars) so a buggy or
# hostile client can't fill the column with megabytes of header data.
_MAX_REASON_LENGTH = 200


def _sanitize_reason(raw: str | None) -> str:
    """Strip control characters + cap length. Mirrors the plugin's own
    sanitise pass (Replace CR/LF, substring 0..200) so the wire-side
    contract is enforced on both ends.

    Returns "" when input is empty/None — caller decides whether that's a
    400 (missing header) or accepted (we treat empty as missing here)."""
    if not raw:
        return ""
    cleaned = raw.replace("\r", "").replace("\n", "").strip()
    if len(cleaned) > _MAX_REASON_LENGTH:
        cleaned = cleaned[:_MAX_REASON_LENGTH]
    return cleaned


def _insert_tamper_report_sync(
    body: IngestRequest,
    *,
    reason: str,
    world: str,
    uploader_logger_name: str,
    uploader_discord_id: str,
    uploader_discord_name: str,
    payload_json: str,
) -> int:
    """Synchronous wrapper around the DB insert. Runs on the executor pool
    (via run_sync) so the async event loop isn't blocked by sqlite I/O —
    matches the pattern used by ``_ingest_payload_sync``."""
    conn = parses_db.init_db()
    try:
        report_id = parses_db.insert_tamper_report(
            conn,
            world=world,
            act_encid=body.encounter.encid,
            title=body.encounter.title or "",
            zone=body.encounter.zone,
            started_at=_parse_unix_seconds(body.encounter.starttime),
            ended_at=_parse_unix_seconds(body.encounter.endtime),
            duration_s=int(body.encounter.duration or 0),
            total_damage=int(body.encounter.damage or 0),
            encdps=float(body.encounter.encdps or 0.0),
            reason=reason,
            reported_at=int(time.time()),
            uploader_logger_name=uploader_logger_name,
            uploader_discord_id=uploader_discord_id,
            uploader_discord_name=uploader_discord_name,
            guild_name=None,  # not resolved on tamper-reports — admins see the raw payload
            payload_json=payload_json,
        )
        conn.commit()
        return report_id
    finally:
        conn.close()


def _parse_unix_seconds(value: str | None) -> int:
    """Parse the plugin's ISO-8601-with-Z timestamps into unix seconds.

    The plugin emits ``yyyy-MM-ddTHH:mm:ssZ`` for both starttime and
    endtime; the older "yyyy-MM-dd HH:mm:ss" form (no T, no Z) is also
    accepted for compatibility with the test fixtures. Returns 0 on
    anything that doesn't parse — tamper reports are evidence, not
    leaderboard rows, so a malformed timestamp shouldn't reject the
    audit insert.
    """
    if not value:
        return 0
    from datetime import datetime

    s = value.strip()
    if not s:
        return 0
    # Normalise the plugin's "Z" suffix to "+00:00" so fromisoformat
    # accepts it on every Python version.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # The old test fixture form uses a space separator instead of "T".
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        from datetime import UTC

        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


@router.post(
    "/parses/tamper-report",
    response_model=TamperReportResponse,
    status_code=201,
)
@limiter.limit("60/minute")
async def report_tamper(
    request: Request,
    body: IngestRequest,
) -> TamperReportResponse:
    """Persist a plugin-detected tamper attempt to the audit table.

    Auth: same as /parses/ingest — Bearer token OR session cookie.
    HMAC validation: strict via _validate_payload_signature (token-auth
    paths require X-Lexicon-Signature; mismatch is 401).

    Reason header is required. Logger_name is validated to the same
    1-15 letter rule the ingest endpoint applies — keeps malformed
    payloads out of admin search.

    Logger_server is recorded verbatim (after shape sanitisation) but
    NOT gated against ALLOWED_SERVERS — a tamper attempt from an
    unallowed server is still evidence worth surfacing. The admin sees
    the raw value in the report.
    """
    user: TokenUser = await require_user_session_or_token(request)
    await _validate_payload_signature(request, user)

    # Reason: required, sanitised, capped.
    raw_reason = request.headers.get(PLUGIN_TAMPER_REASON_HEADER)
    reason = _sanitize_reason(raw_reason)
    if not reason:
        raise HTTPException(
            status_code=400,
            detail=f"{PLUGIN_TAMPER_REASON_HEADER} header is required.",
        )
    if reason not in KNOWN_TAMPER_REASONS:
        # Accept unknown codes for forward-compat with future plugin
        # versions, but log so the maintainer notices a new heuristic
        # they may want to wire admin-UI styling for.
        _log.info(
            "[tamper-report] unknown reason code: %r (token user=%s)",
            reason,
            user.get("id"),
        )

    # Logger_name: validate to the same EQ2 character-name shape ingest
    # uses. Reject malformed payloads (1-15 letters only).
    uploader = (body.logger_name or "").strip()
    if not uploader or _validate_character_name(uploader) is None:
        raise HTTPException(
            status_code=400,
            detail="logger_name must be 1-15 letters (the EQ2 character-name shape).",
        )

    # Logger_server: best-effort sanitise but don't gate. A tamper attempt
    # from a non-allowlisted server is still evidence; the admin reads
    # the value as-is.
    raw_server = (body.logger_server or "").strip()
    sanitized_server = _sanitize_world(raw_server) if raw_server else None
    world_for_report = sanitized_server or raw_server or "unknown"

    # Resolve uploader discord identity from the auth path. Session and
    # token both surface `id` (Discord ID) and `username`. discord_name
    # is the friendly display name when token auth surfaced it.
    discord_id = str(user.get("id") or "")
    discord_name = str(user.get("discord_name") or user.get("username") or "")

    # Serialise the body for the evidence column. Pydantic v2's
    # model_dump_json is the canonical wire-shape; cap not enforced
    # here because IngestRequest's field validators already constrain
    # combatants / damage_types / attack_types sizes.
    payload_json = body.model_dump_json()

    try:
        report_id = await run_sync(
            _insert_tamper_report_sync,
            body,
            reason=reason,
            world=world_for_report,
            uploader_logger_name=uploader,
            uploader_discord_id=discord_id,
            uploader_discord_name=discord_name,
            payload_json=payload_json,
        )
    except sqlite3.Error:
        _log.exception(
            "[tamper-report] DB error persisting report: user=%s reason=%s encid=%s",
            discord_id,
            reason,
            body.encounter.encid,
        )
        # Don't surface DB details to the client.
        raise HTTPException(status_code=500, detail="Could not persist tamper report.") from None

    _log.info(
        "[tamper-report] stored: id=%d reason=%s user=%s logger=%s world=%s encid=%s",
        report_id,
        reason,
        discord_id,
        uploader,
        world_for_report,
        body.encounter.encid,
    )
    return TamperReportResponse(id=report_id, reason=reason)
