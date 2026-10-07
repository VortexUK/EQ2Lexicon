# Upload integrity

Code: `backend/server/api/parses/ingest.py`, `backend/server/api/parses/tamper_report.py`,
`backend/server/parses/plausibility.py`, the tamper-report panel in
`backend/server/api/admin.py`, and `frontend/src/pages/admin/TamperReportsTable.tsx`.

Parse uploads feed public leaderboards, so the ingest path layers several
independent checks. None of them alone proves a parse is honest; the uploader
holds their own API token and can sign any payload they like. The layers
bound what a dishonest or broken client can do, and route anything
questionable to an admin instead of the board.

| Layer | Stops | Outcome |
|---|---|---|
| Client flood gate (`INGEST_CLIENT_LIMITS`) | a runaway third-party client | 429 before body parse or auth |
| HMAC signature | in-flight tampering, replay with only a stolen token | 401 / 400 |
| Character-name shape | garbage in Census URLs and cache keys | 400 |
| Server allowlist | uploads for servers the site doesn't serve | 400 / 403 |
| Plausibility gate | impossible or absurd numbers | 400 or quarantine |
| Claim binding | filing fights under someone else's character | stored, never attributed or ranked |
| Plugin tamper heuristics | edited or replayed encounters | tamper report, never on the board |

## HMAC signing

Token-authenticated uploads must carry `X-Lexicon-Signature`, the lowercase hex
HMAC-SHA256 of the raw (uncompressed) request body keyed by the bearer token.

| Auth | Header | Result |
|---|---|---|
| token | missing | 401, with the plugin releases URL |
| token | present | must verify; mismatch is 401 |
| session | present | 400 (confused client) |
| session | absent | allowed |

Details that matter when changing this code:

- The header name must match `PayloadSigner.SignatureHeaderName` in the plugin
  repo; changing one side breaks every upload.
- The handler re-reads `request.body()` after FastAPI has parsed it, relying on
  Starlette's body cache. Any middleware that rewrites the body must keep the
  signature over the uncompressed JSON (the gzip middleware does) and must be
  covered by the regression test in `tests/server/test_parses_ingest_hmac.py`.

## Server allowlist

`logger_server` is detected by the plugin from its log path and is required.
Missing → 400 asking the user to update the plugin; malformed → 400; not in
`ALLOWED_SERVERS` (compared case-insensitively, stored in original casing) →
403 listing the allowed servers. The validated value is the parse's `world`.

## Claim binding

The uploader name (`logger_name`) is client-supplied. An upload is
`uploader_verified` only when the token owner holds an approved claim on that
character for that world. Unverified uploads are kept for their uploader but
get no guild attribution and never rank, so a valid account cannot post fights
under a rival guild's roster.

## Plausibility gate

`plausibility.evaluate` runs before any Census or DB work and returns ACCEPT,
REJECT (400) or QUARANTINE. Quarantined uploads are written to
`tamper_reports` with a `server_` reason prefix and the client gets a
normal-looking 201 with `status="quarantined"`, which gives a forger no
calibration feedback. The reasoning behind the thresholds is in
[ADR-upload-plausibility-gate.md](../decisions/ADR-upload-plausibility-gate.md).

## Tamper reports

When the ACT plugin's own heuristics decide a parse was tampered with (a
renamed encounter whose title doesn't match its enemies, a stale encounter, or
recent import activity), it does not upload to the board. It POSTs the same
`IngestRequest` body to `POST /api/parses/tamper-report` with an
`X-Lexicon-Tamper-Reason` header, under the same auth and strict HMAC rules as
ingest.

- Rows go to `tamper_reports` only, never to `encounters`.
- The reason is required, stripped of CR/LF and capped at 200 characters (the
  plugin applies the same rule). Codes outside `KNOWN_TAMPER_REASONS` are
  accepted and logged, so new plugin heuristics need no server change.
- `logger_name` must be a valid character name; `logger_server` is recorded
  after shape sanitisation but is **not** checked against the allowlist — a
  report from an unexpected server is still evidence.
- Timestamps that fail to parse are stored as 0 rather than rejecting the
  report: it is evidence, not a leaderboard row.
- The plugin is fire-and-forget here and never shows the response, so the
  response is minimal (`id`, `reason`) and diagnostics go to the logs and
  the `tamper_report.received` audit event.

Server-side quarantines from the plausibility gate share the table (their
reasons start with `server_`), so admins review both in one place.

### Admin panel

All routes are admin-only and scoped to the active server (`current_world()`):

- `GET /api/admin/tamper-reports` — pending reports by default, optionally
  filtered by reason. `pending_count` is always the total unacknowledged count,
  whatever filter is in view, so the panel header needs no second request.
- `POST …/{id}/acknowledge`, `POST …/acknowledge-batch` (up to 500 ids) and
  `POST …/acknowledge-all` — one-way; the acknowledging admin is stamped on
  the row. Acknowledge-all exists for spam floods that would take too many
  batch calls.
- `POST …/purge-acknowledged` — hard-deletes acknowledged reports only;
  pending reports are never deleted.

## Moderation is sticky

Soft-deleting (hiding) a parse is the moderation action. A re-upload of the
same encounter is skipped and never un-hides it; only an explicit unhide (by an
admin, the uploader or a guild officer, the same rule as hiding)
restores it. Deletes always name explicit encounter ids, and each id is
authorised on its own row.
