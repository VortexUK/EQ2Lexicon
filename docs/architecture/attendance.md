# Raid attendance

The guild Attendance tab shows who raided on each raid night and rolls that
up into a per-player attendance matrix. This page explains how raw uploads
become sessions, how each character is categorised, and how the Discord bot's
voice check fits in.

| Piece | File |
|---|---|
| Ingest + read routes | `backend/server/api/attendance.py` |
| Store (users schema) | `backend/server/db/attendance.py` + `attendance.sql` |
| Category derivation (pure, no I/O) | `backend/server/attendance.py` |
| Voice poller | `backend/bot/cogs/voice_attendance.py` |

## Ingest

EQ2Parser posts cumulative snapshots to `POST /api/attendance/ingest`: the
current raid members and the online guild members, each with a
`first_seen` / `last_seen` pair, plus the zones seen. It uses the same upload
contract as parse ingest (bearer token, HMAC over the uncompressed JSON, gzip
handled by middleware). The server resolves and verifies the uploader's guild
itself; the client never asserts its own guild.

Before storing, the route probes the guild's raid schedule over the snapshot's
window. The resulting `scheduled` flag and `team_index` are frozen on the
session, so later schedule edits never rewrite history.

## Sessions and the merge-gap window

Several officers often run the parser at once, each uploading snapshots. The
store's `apply_snapshot` folds all of them into one session per raid night:

- **Identity** is `(world, guild_name, session_day, seq)`. `session_day` is the
  UTC date of `started_at - 6h` (an evening rollover, matching the
  availability calendar), and `seq` separates genuine double-headers.
- **Merge gap**: a snapshot whose observation window lies within
  `MERGE_GAP_S` (3 hours) of an existing session merges into it; otherwise it
  starts a new session.
- **Window follows the raid**: the session's start/end come from raid-member
  times. Online-only snapshots can create a session from their own window, but
  a merge only *widens* an existing window when the snapshot shows at least
  `MIN_RAID_FOR_WINDOW` (12) raid members. Smaller snapshots, such as a parser
  left running overnight uploading online guild members, still merge their
  observations but cannot stretch a finished raid.
- **Runaway guard**: no merge may stretch a session past `MAX_SESSION_SPAN_S`
  (16 hours).
- **Concurrency**: the find-or-create runs under a transaction-scoped advisory
  lock per (world, guild), so two uploaders cannot both create a session.
- **Order independence**: the observation upsert keeps `MIN(first_seen)` /
  `MAX(last_seen)`, so arrival order of uploads never matters.

Officers can correct a session's start/end afterwards (to clean up a bad
merge); `session_day` and `seq` stay fixed when they do.

## Observations

`attendance_observations` holds one row per (session, name, kind):

| kind | `character_name` holds | Source |
|---|---|---|
| `raid` | character name | parser snapshot: in the raid |
| `online` | character name | parser snapshot: online in guild |
| `voice` | **Discord user id** | bot voice poller |

`voice` rows never become characters; they only mark the owning player as
having been in the raid voice channel.

### Voice cross-check

For each Discord server linked with `/lexicon voice`, the bot polls every
120 seconds. It asks the store for a live session
(`find_live_session(world, guild, now)`, the same merge-gap window as ingest).
Only when one exists does it read the voice channel's members and record a
`voice` observation per Discord id. With no live session the idle cost is one
query per linked guild and no Discord API calls. Voice observations are
deleted after `VOICE_OBSERVATION_RETENTION_DAYS` (90) by the parse-cleanup
loop.

## Categories

Categories are derived at read time and never stored, so later edits to
roles, claims or availability always apply retroactively. Inputs gathered by
the route:

- observations for the session;
- raid-planner roles (`raider` / `raid_alt`, placeholders included);
- character claims (character → Discord id) and primary claims;
- availability for the session day (per user, and per character merged
  newest-edit-wins);
- the session's frozen `scheduled` flag and window;
- officer overrides and officer-authored timelines.

Per character, the first matching rule wins:

| Category | When |
|---|---|
| `present` | seen in the raid |
| `sat_out` | rostered and online, but not in the raid (the bench) |
| `afk` | rostered and declared AFK, not seen |
| `awol` | rostered raider, scheduled session, not seen, no AFK declared |
| `absent` | everything else |

Observed behaviour beats declaration: a character declared AFK who shows up
is `present`, and one who is online is `sat_out` because they were
demonstrably available. A per-character availability entry decides alone;
only characters without one fall back to their owner's calendar.

Each character also gets a timeline of segments. The raid window is
`present`; a rostered character's online time before or after it is
`sat_out` when it lasts at least 10 minutes (shorter is login noise). Derived
segments are clipped to the session window. Officers can:

- **override** a category, which beats every derived category and adds the
  character even if never observed;
- **write a timeline**, which replaces the derived one; without an override
  the category becomes the best state in that timeline. Manual timelines are
  never clipped.

Never-observed, never-corrected rostered non-raiders are dropped, since there
would be nothing for an officer to delete. Raiders keep their no-show row:
that is the AWOL signal.

## Per-player rollup and AWOL

Characters group by claim owner. A player takes the **best** category across
their characters, so a raid alt attending credits its owner, and the player
row carries `in_voice` from the voice observations. Raid alts are never
expected, so they are never AWOL. The site shows players who were in voice but
not in game alongside AWOL.

`resolve_mains` picks each player's raid main: their claimed `raider`
(primary claim first, else alphabetical), falling back to their primary
claim. Its `char_mains` table maps every rostered character, and every other
claimed character of a player with a main, onto that main. EQ2Parser uses it
for DKP substitution, so a character dual-boxed from a second account cannot
earn DKP twice.

## The summary matrix

`summarize_attendance` builds one row per player (unclaimed characters are
their own row) with one cell per session. Attendance percent is
`(present + sat_out) / number of sessions shown`: a benched raider showed up,
so the bench counts as attendance, and a session with no cell counts like an
absent one. Rows absent in every session are dropped.
