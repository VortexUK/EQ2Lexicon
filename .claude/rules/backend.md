---
paths:
  - "backend/**"
---

# Backend conventions

## Logging conventions

- **Module-level binding**: `_log = logging.getLogger(__name__)`.
- **Lazy `%s` formatting**: `_log.info("foo %s", x)` — never f-strings inside log calls.
- **Bracketed prefix per module**: `[lowercase-with-hyphens]` (e.g. `[cache]`, `[census-refresh]`). Helps grep by component.
- **For audit events**: use `audit_log("snake_case_action", actor=..., **fields)` from `backend.server.core.audit_log`. Don't hand-roll `_log.info("[audit] …")`.
- **Levels**: WARNING for security signals (HMAC mismatch, invalid token); INFO for audit-trail / startup config / state changes; DEBUG for per-request noise; ERROR/exception for "needs investigation". Recoverable Census flakes are WARNING, not ERROR (per the 2026-05-30 audit).
- **Env vars**: `LOG_LEVEL` (default `INFO`) and `LOG_FORMAT` (`text` default, `json` for Railway) are read by `configure_logging()` in `backend/core/logging_config.py` at startup.
- **Sensitive values that NEVER appear inside a log-call argument list**: bearer tokens, HMAC signature bytes, `DISCORD_CLIENT_SECRET`, OAuth `access_token`s, the `raw` token from `mint_api_token`.
- **Floods produce one line, not one per request** (2026-09-28, after a third-party client's ~2,000 tracebacks/hour buried the audit trail): an unhandled exception is caught by the outermost `UnhandledErrorMiddleware` (`core/unhandled_errors.py`) → one ERROR line (method, path, leaf exception, deepest frame in our code, request_id) + JSON 500, full traceback only once per (path, exception) per 5 min via `core/log_coalesce.py`. 429s log once per (client, path) per minute in `app.py:_rate_limit_handler` (slowapi's own per-request warning is pinned to ERROR). Per-client-app ingest budgets live in `core/client_throttle.py` (`INGEST_CLIENT_LIMITS`). A client-side payload bug (duplicate rows) is a 422 with a WARNING naming the client, never a 500. Before adding a per-request log line, ask what it looks like at 3,600/hour — if the answer is "noise", key it through the coalescer.

## Testing notes

- Roughly one full `pytest` run in three ends `N passed, 1 error`: a RuntimeError at teardown on a different random test each time, from a background refresh task outliving its test loop. Rerun once. Investigate only if the same test errors again or anything is FAILED.
- Piping pytest into `tail` hides its exit status; read the summary line for `failed` or `error`.
- Tests never call live Census: use the in-memory fixtures or a mock.
