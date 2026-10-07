# Logging internals

CLAUDE.md's "Logging conventions" section lists the rules for writing a log
line (module logger, lazy `%s`, bracketed prefix, `audit_log` for audit
events, levels, secrets, floods). This page explains the machinery behind
those rules: how request context reaches every line, how audit records are
shaped, and how floods are coalesced.

## Configuration

`configure_logging()` (`backend/core/logging_config.py`) installs a single
root `StreamHandler` and replaces whatever handlers a framework already
installed, so calling it more than once is safe. `main.py`, the app lifespan
and the bot's `setup_hook` all call it.

- `LOG_LEVEL` (default `INFO`; invalid values fall back to `INFO`).
- `LOG_FORMAT`: `text` (default) or `json`. Text lines look like:

  ```
  2026-05-30 14:23:01.123  INFO     [req=abc12345 user=287… world=Varsoon]  [admin] Claim approved: …
  ```

  JSON mode writes one flat object per line (`ts`, `level`, `logger`,
  `message`, plus every non-standard record attribute, including `extra=`
  fields and the request-context fields, at the top level, and `exc` when
  there is a traceback).
- Third-party loggers (`discord*`, `aiohttp.access`, `uvicorn.access`) are
  pinned to WARNING so a library default change can't flood the logs.
  `slowapi` is pinned to ERROR because it warns on every rejected request;
  the app's own 429 handler logs instead (see below).
- One `eq2.startup` INFO line announces the active level and format.

## Request context on every line

`backend/server/core/request_context.py` defines three `ContextVar`s:
`request_id_var`, `user_id_var` and `world_var`. Context variables are
task-local in asyncio, so everything awaited inside a request handler sees
the request's values without any argument passing.

- `RequestContextFilter` is attached to the root handler. It stamps
  `request_id`, `user_id` and `world` onto every `LogRecord` (from any
  logger, including plain `logging.getLogger(__name__)`) unless the caller
  already set them via `extra=`. Outside a request every field is `-`, so
  lines stay parseable for background tasks, scripts and tests.
- `get_logger(name)` returns a `LoggerAdapter` that injects the same fields.
  It is optional; the filter already covers plain loggers.

Who sets the variables:

- `RequestContextMiddleware` (`request_context_middleware.py`) sets
  `request_id` (an inbound `X-Request-ID` of up to 64 characters is honoured,
  otherwise a 16-hex-character id is minted) and `user_id` (from
  `request.session["user"]["id"]` when a decoded session is available). It
  echoes `X-Request-ID` on the response and resets the variables on exit so
  later background tasks don't inherit stale values. It also stores the id
  on `request.state.request_id`, which outlives the reset so the outermost
  error middleware can still report it.
- `ServerContextMiddleware` (`backend/server/server_context.py`) sets
  `world_var` to the resolved server's world for the request.

Starlette runs `add_middleware` calls in reverse order (the last one added is
the outermost), so the registration order in `create_app` matters; the
comments there spell out which layer must see which.

Context variables do not cross processes. The app asserts
`WEB_CONCURRENCY=1` at startup; a multi-worker deployment would need the
request id carried between workers explicitly.

`run_sync` (`backend/server/core/executor.py`) copies the caller's context
into the worker thread, so log lines from thread-pool work keep the
request's fields.

## Audit records

`audit_log(action, actor, **fields)` (`backend/server/core/audit_log.py`) is
the only way to write an audit-trail line.

- It logs on the dedicated `eq2.audit` logger, fixed at INFO regardless of
  `LOG_LEVEL`, so audit rows are never filtered out. Aggregators can route
  that logger name to its own sink or index.
- The message is short (`audit: <action> actor=<actor>`); the data lives in
  `extra=` as flat fields: `action`, `actor` (`-` for system events),
  `request_id`, `world`, plus every caller field. Dashboards filter on those
  fields, so keep action names stable snake_case (`claim_approved`,
  `role_granted`, `parse_purged`).
- Every value goes through `scrub()` (`backend/core/log_safety.py`), which
  replaces CR and LF with spaces so a hostile name can't forge extra log
  lines. Use `scrub()` yourself for any user-supplied value in an ordinary
  log line too.
- Field names that collide with `LogRecord` attributes (`name`, `msg`,
  `module`, …) are renamed with a trailing underscore rather than raising.
- `grep 'audit_log("'` finds every privileged event in the codebase.

## Coalescing floods

`backend/server/core/log_coalesce.py` provides `Coalescer.allow(key,
every_s)`, which returns `(log_it, suppressed)`: the first occurrence of a
key in each window is logged, later ones are counted, and the next logged
line can append "(+N suppressed)". The shared `coalescer` instance is
process-local and thread-safe. Its memory grows with the number of distinct
keys, so a key must have low cardinality (path + exception type, path +
client identity), never raw request data.

Current users:

- The 429 handler (`app.py:_rate_limit_handler`) logs one WARNING per
  (path, client identity) per minute with the suppressed count.
- `UnhandledErrorMiddleware` coalesces full tracebacks (below).
- `backend/census/failures.py` has its own per-endpoint `should_log` for
  Census non-200 responses: one WARNING a minute per endpoint.

## Unhandled exceptions

`UnhandledErrorMiddleware` (`backend/server/core/unhandled_errors.py`) is the
outermost ASGI layer. An app-level `Exception` handler is not enough:
Starlette's `ServerErrorMiddleware` re-raises after calling it, so uvicorn
still prints "Exception in ASGI application", and because the
`BaseHTTPMiddleware` layers run inside an anyio TaskGroup the dump is an
`ExceptionGroup` with every middleware frame repeated (hundreds of lines per
request).

The middleware instead catches the exception, unwraps any `ExceptionGroup`
to its first leaf (the error a developer actually wants, such as
`psycopg.errors.UniqueViolation`), and logs:

- every occurrence: one ERROR line with method, path, leaf exception type and
  message (truncated), the deepest traceback frame in our code
  (`file:line in func`) and the request id;
- the full traceback only once per (path, exception type) per 5 minutes,
  with the count of one-liners suppressed since.

It then sends a JSON 500 (`{"detail": "Internal Server Error",
"request_id": …}`) with `X-Request-ID`, and does not re-raise, so nothing
downstream logs it again. If the response has already started, it re-raises,
since there is no way to send a fresh status.

A client-side payload bug should be a 4xx with a WARNING naming the client,
never a 500. Per-client-app ingest budgets live in
`backend/server/core/client_throttle.py` (`INGEST_CLIENT_LIMITS`).
