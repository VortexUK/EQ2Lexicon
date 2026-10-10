---
name: prod-logs
description: Read production logs from Railway, filtered by level and pattern with repeated lines collapsed. Use to check a deploy, investigate a reported error or outage, or confirm a background job ran.
---

# Production logs

Raw Railway logs are long and repetitive. This wrapper filters and collapses them before
they reach the conversation.

```bash
uv run --frozen python scripts/tools/railway_logs.py                       # warnings and errors, recent
uv run --frozen python scripts/tools/railway_logs.py --level error
uv run --frozen python scripts/tools/railway_logs.py --grep "rankings" --level info
uv run --frozen python scripts/tools/railway_logs.py --since 2h --max 40
uv run --frozen python scripts/tools/railway_logs.py --help                # the options this CLI version supports
```

A header line reports how many lines were fetched, matched and grouped. Each line after
it is `time level tag  message  (xN, last time)`; `xN` means N similar lines were
collapsed into one, with numbers, ids, IPs and quoted strings replaced by placeholders.
Times are UTC. Useful extras: `--sort count` to rank the noisiest messages over a long
window, `--exact` to stop similar groups being folded together when you need the detail
(for example which Census collection timed out), `--build` for build logs.

## Use

- Start narrow (`--level error`, or a `--grep` on the component prefix) and widen only if
  that shows nothing.
- Log lines carry a bracketed component prefix (`[startup]`, `[cache]`, `[census]`,
  `[census-health]`, `[rankings]`, `[retention]`, `[raid-live]`, ...). Grep for the prefix
  of the area you are investigating.
- Unhandled exceptions are one ERROR line with the method, path, leaf exception and
  `request_id`; the full traceback is logged once per (path, exception) per five minutes.
  Grep the `request_id` to follow one request.
- 429s are logged once per (client, path) per minute.

## After a deploy

1. Look for the migration lines at startup, then for the app accepting requests.
2. `--level warning --since 15m` should be quiet apart from known Census flakes.
3. `max clients reached in session mode` means the connection pooler is full, not that
   the code is broken: see `docs/runbooks/deploy.md`.

## Boundaries

The script only reads logs. Do not use the Railway CLI to redeploy, restart, change
variables or relink from a session unless the user asks for that specific action. The
CLI is located from `RAILWAY_CLI` or `PATH`; if it is missing, say so and ask.

The script redacts tokens, passwords and connection strings. A line that occurred only
once is printed with its real values, which can include a Discord user id or a client
IP. That is personal data: do not copy it into a reply, a commit or an issue; describe
the event instead.

Production currently logs in the text format, so the level and the `[tag]` are parsed
from the message itself and Railway's own level filter is not used.
