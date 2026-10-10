---
name: census-query
description: Run one Daybreak Census API query with a narrow field projection and compact output. Use to inspect what Census returns for a character, guild, item, spell, recipe or quest, or to check a Census field name before writing code against it.
---

# Census query

An unprojected Census document runs to thousands of lines. This script requires a
projection and truncates what comes back.

```bash
uv run --frozen python scripts/tools/census_q.py character name.first=Somename locationdata.world=Wuoshi --show name.first,type.level,type.class
uv run --frozen python scripts/tools/census_q.py guild name=Someguild world=Wuoshi --show name,level,members
uv run --frozen python scripts/tools/census_q.py item displayname=^Screaming --show id,displayname,tier --limit 5
uv run --frozen python scripts/tools/census_q.py spell crc=123456 --show name,tier_name,level
```

`--show` is required; `--raw` exists for the rare case where you need the full document,
and should be combined with a small `--max-chars`. Other options: `--limit` (default 5),
`--sort field:-1`, `--resolve`, and `--count` to get the number of matches without
fetching them. The request is echoed with the service id masked; exit code 1 means no rows.

## Census behaviour worth knowing before trusting a result

- **Recently active characters only.** Census returns character data only for players who
  logged in recently. An empty result for a real character is normal, and is why
  combatant snapshots at parse ingest resolve partially.
- **Class filters need the numeric id.** Filter on `type.classid`; a string `type.class`
  filter silently returns nothing. The id is the classes schema's `census_classid`, which
  is not the same column as `icon_id`.
- **Never `c:show=achievements`.** It drags the 500-plus item list and the lifetime
  statistics with it. Project the scalar you need (`achievements.points`).
- **Back-to-back sorted queries get dropped.** A sorted query that returns nothing may
  succeed on retry; the app retries leaderboard queries for this reason.
- **Item ids from game links are signed 32-bit.** Add `2**32` to a negative id.
- **Spell names on old-era servers.** Census only has the post-2010 "Name + numeral" spell
  names. Servers in an era before that log era-specific names that Census never returns,
  so matching log text to Census spells by name is unreliable there.
- The default service id is rate limited. A timeout or 5xx is retried once.

## In application code

Queries belong in `backend/census/client.py` (`CensusClient`); parsing in
`backend/census/item_parser.py` and `backend/census/models.py`. URL shapes for the common
lookups are in `.claude/rules/census.md`. A request path never awaits Census: it serves
the stored copy and lets the background refresh update it.
