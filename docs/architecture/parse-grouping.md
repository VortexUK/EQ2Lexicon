# Parse grouping

Code: `backend/server/api/parses/list.py` (+ `list.sql`). Reused by
`backend/server/api/rankings.py` (primary boss kills) and
`backend/server/parses/cleanup.py` (which upload of a fight to keep).

Several raiders in the same fight each upload their own ACT capture of it.
The site shows one **fight** per real encounter, with every raider's upload
("mirror") nested under it. Grouping is computed on read; nothing about a group
is stored.

## Mirror grouping

`_group_into_fights` greedily groups uploads in `started_at` order. Two uploads
are the same fight only when ALL of these hold:

- they come from **different uploaders** — one raider cannot mirror their own
  fight, so two uploads from the same person are two fights (a same-encid
  re-upload is already deduplicated at ingest);
- their **guild and title** match;
- some pair of start times is within `PARSE_MIRROR_WINDOW_S` (60 s) — checked
  against every member, so a late straggler still attaches;
- their **top-N ally rosters mutually contain** each other: each upload's top-N
  players by encDPS appear somewhere in the other's full player list. N is 3 if
  either upload has 7+ players, else 2.

The top-N gate exists for the case of two groups from the same guild pulling
the same boss at the same time: guild, title and time all match, but the
rosters do not.

The **canonical** upload is the longest-duration one (the raider whose ACT
captured the most of the fight); its fields are the fight's top-level fields.
New uploads are compared against the current canonical only, so membership is
transitive through the canonical, which can change as longer uploads join.
This is a deliberate simplification; the case it gets wrong (a join that
matches the canonical but would fail against an earlier member) is rare.

Performance notes:

- Candidates are bucketed by `(title, guild)`; only same-bucket groups are
  scanned. A flat scan is quadratic over the whole history.
- `_group_into_fights` prefetches every candidate's ordered player roster in
  one `= ANY` statement (`ally_rosters_bulk`) and exposes it through a
  ContextVar, so the top-N helpers don't issue four queries per compared pair.
  Called directly, the helpers fall back to per-encounter queries.
- Roster order is encDPS DESC, name ASC, so ties always pick the same top-N.

## The list endpoint

`GET /parses` takes a **fight** limit (max `PARSE_LIST_MAX_LIMIT`), but SQL can
only cap raw uploads. The inner cap is `limit × PARSE_INNER_CAP_MULTIPLIER` (30)
with a floor of `PARSE_INNER_CAP_FLOOR`, enough for the worst-case 24-mirror
raid. Pagination uses `before=<started_at>`; `next_before` is set when either
the fight count or the raw SQL window overflowed.

The list+classify+group build is cached per filter tuple (`_LIST_CACHE`):
60 s fresh, served stale for up to 6 h with a background rebuild,
single-flight per key, and prewarmed for each world's default view at startup.
Mutations that change what the list shows (delete / hide / purge) call
`invalidate_parses_list_cache()`; uploads deliberately do not — raid nights
arrive in bursts and the 60 s TTL bounds the lag.

Per-upload delete permissions are computed from cached guild rosters only. A
cold roster means officer delete buttons don't render on that load; the
DELETE routes still authorise against a full lookup.

## Lazy classification backfill

Every reader that filters on `is_player = 1` first ensures the encounter's ally
rows are classified (`_ensure_classified`, or the batched
`encounters_needing_classification` + `_classify_now` pair used by the list
build and the rankings rebuild). Unclassified encounters would otherwise
report a player count of 0.

- The probe is scoped to `ally = 1`. Enemy rows stay NULL by design, so an
  unscoped probe would see every encounter as unclassified and every read
  would rewrite the same rows.
- The backfill is a write riding a read path. A `psycopg.Error` (for example a
  deadlock with concurrent ingest) is logged, rolled back, and retried on the
  next read; it never fails the response.

## Zone classifier

`_classify_zone` buckets a parse's zone into `raid` / `dungeon` / `other` for
the parses page's Guild → Category hierarchy and for the retention tiers in
`cleanup.py`. It is derived from the rankings page's curated zone trees
(`rankings._cached_zones_data`), so a zone counts as raid or dungeon exactly
when it appears in the rankings dropdowns: the right zone type **and** at least
one curated encounter.

Lookup: empty / `(unknown zone)` → other; then an exact lowercase match; then
alias resolution through `rankings._zone_canonical_map` and a retry on the
canonical name; otherwise other. If a curator ever tags one zone as both raid
and dungeon, dungeon wins (an arbitrary choice).

The map is built lazily and cleared by `rankings.invalidate_zones_cache`, so
every curator edit that refreshes rankings refreshes the classifier.
`rankings.py` imports `list.py`, so `list.py` reaches back through small
wrappers that import lazily; tests patch those wrappers.
