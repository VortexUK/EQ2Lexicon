# Server registry and expansion rollover

One deployment serves several EverQuest 2 servers, each on its own subdomain
(`varsoon.eq2lexicon.com`, `wuoshi.eq2lexicon.com`, ...). This page explains
where per-server settings live and how a server moves to its next expansion.

## The registry

The `servers` table in the users schema holds one row per server:

| Column | Meaning |
|---|---|
| `world` | Census world name (the key everything else scopes by). |
| `subdomain` | Host prefix that selects this server. |
| `display_name` | Name shown in the UI and the server switcher. |
| `max_level` | Current character level cap. |
| `current_xpac` | Short code of the live expansion (`RoK`, `TSO`, ...). |
| `current_xpac_started_dt` | When `current_xpac` went live — the rankings era-lock cutoff. |
| `launch_dt` | Server launch instant. |
| `next_xpac` / `next_xpac_dt` | A scheduled rollover (drives the countdown banner). |
| `is_default` | The fallback server when no subdomain matches. |

Store: `backend/server/db/servers.py`. Rows are admin-editable; the
`EQ2_WORLD` / `SERVER_*` / `LAUNCH_DT` env vars only seed the first row.

### Request resolution

`backend/server/server_context.py` loads the registry into memory at startup
(`load_registry()`) and reloads it after every admin edit or rollover.
`ServerContextMiddleware` maps the request `Host` to a row and stores it on a
contextvar; route code calls `current_world()` / `current_server()`. Without a
match the default server is used (the `is_default` row). Non-production
environments also accept an `X-Server` header or `?server=` query parameter.

Because the active server lives on a contextvar, work moved onto a thread must
copy the caller's context (`backend/server/core/executor.run_sync` does this),
and background tasks that run outside a request must pass `world` explicitly.

The registry is in-process memory: it assumes a single web worker.

## Expansion rollover

`backend/server/xpac_rollover.py` runs `poll_loop()` from the app lifespan and
checks every minute. A row is due when `next_xpac` is set and `next_xpac_dt`
has passed. Rolling over a row, in one update:

- `current_xpac` becomes `next_xpac`;
- `max_level` becomes the expansion's cap from `XPAC_MAX_LEVEL` (game facts
  kept in code; an unknown short code keeps the existing cap and logs a
  warning);
- `current_xpac_started_dt` is stamped with the **scheduled** `next_xpac_dt`,
  not the time the loop noticed. A restart or a missed poll must not move the
  era-lock cutoff;
- `next_xpac` / `next_xpac_dt` are cleared, so the countdown banner disappears.

Each flip is audited (`xpac_rollover`) and the in-memory registry reloads so
`current_world()` consumers see the new era immediately.

### What the era lock affects

`current_xpac_started_dt` is read by `backend/server/api/rankings.py`
(`_era_lock_for` / `_apply_era_lock`). Once a server has rolled over, a raid
kill in a zone from an earlier expansion only ranks if it was ingested before
the cutoff, which freezes out-of-era leaderboards. In-era zones, zones with no
expansion info, and servers that have never rolled over are unaffected.

Uploads, parse pages, attendance and Census-driven guild progression ignore
the era lock entirely.
