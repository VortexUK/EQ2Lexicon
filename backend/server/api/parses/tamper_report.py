"""POST /parses/tamper-report — audit channel for plugin-detected tamper.

Body is an IngestRequest; headers are the ingest auth + HMAC plus
``X-Lexicon-Tamper-Reason``. Rows go to ``tamper_reports`` for admin review
and NEVER to ``encounters``, so the parse cannot reach a leaderboard.
"""

from __future__ import annotations

import logging
import time

import psycopg
from fastapi import HTTPException, Request

from backend.core.log_safety import scrub as _safe_for_log
from backend.server.api.parses import router
from backend.server.api.parses.ingest import _validate_payload_signature
from backend.server.api.parses.models import IngestRequest, TamperReportResponse
from backend.server.auth_deps import require_user_session_or_token
from backend.server.core.audit_log import audit_log
from backend.server.core.executor import run_sync
from backend.server.core.session_user import TokenUser
from backend.server.core.validation import sanitize_world as _sanitize_world
from backend.server.core.validation import validate_character_name as _validate_character_name
from backend.server.limiter import limiter
from backend.server.parses.db import store as parses_db

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
    (via run_sync) so the async event loop isn't blocked by sync DB I/O —
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
    endtime; "yyyy-MM-dd HH:mm:ss" (no T, no Z, read as UTC) is also
    accepted. Returns 0 on
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
    # Also accept a space separator instead of "T".
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

    Same auth + strict HMAC as /parses/ingest. logger_server is NOT gated
    against ALLOWED_SERVERS — a report from an unallowed server is still
    evidence.
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
        # Accept unknown codes (forward-compat with newer plugins), but log
        # so a new heuristic gets noticed. No discord_id here — the audit
        # log and the row's uploader_discord_id carry the actor.
        _log.info(
            "[tamper-report] unknown reason code: %s",
            _safe_for_log(reason),
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
    except psycopg.Error:
        # Drop discord_id from the diagnostic log — encid + reason are
        # the actionable bits, and the actor's identity is in the DB
        # row when the insert succeeded (and on every other call in
        # the same session via the request_id context).
        _log.exception(
            "[tamper-report] DB error persisting report: reason=%s encid=%s",
            _safe_for_log(reason),
            _safe_for_log(body.encounter.encid),
        )
        # Don't surface DB details to the client.
        raise HTTPException(status_code=500, detail="Could not persist tamper report.") from None

    # Successful receipt of a tamper report IS an audit event.
    audit_log(
        "tamper_report.received",
        actor=discord_id,
        report_id=report_id,
        reason=reason,
        logger=uploader,
        world=world_for_report,
        encid=body.encounter.encid,
    )
    return TamperReportResponse(id=report_id, reason=reason)
