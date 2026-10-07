# Discord bot

The bot (`backend/bot/`) runs in the same process as the web app. `bot.py`
registers the cogs, syncs slash commands, and owns the shared app-command error
handler. This page covers the parts that span several files: how a Discord
server maps to an EQ2 guild, and the two background pollers that act on that
mapping.

## Guild context (`backend/bot/guild_context.py`)

Each Discord server can be linked to one EQ2 `(world, guild_name)` pair. Links
live in the `discord_guild_links` table in the users schema
(`backend/server/db/discord_links.py`). Every world-aware command calls
`resolve_guild_context(discord_guild_id)` and gets a `GuildContext`
(`world`, `guild_name`, `voice_channel_id`, `linked`).

- DMs and unlinked servers resolve to `FALLBACK_WORLD` (`"Wuoshi"`, hardcoded
  on purpose rather than another env var) with no guild.
- The env `WORLD` setting is not used by the bot for context.
- Any `psycopg.Error` during the lookup (the table not yet migrated on a fresh
  deploy, or connection trouble while the bot races the web lifespan) degrades
  to the fallback. A slash command must never crash because of the registry.

Officer checks for bot commands reuse the site's officer resolution, run under
the linked world's server context.

## `/lexicon` link commands (`backend/bot/cogs/lexicon.py`)

| Command | Effect |
| --- | --- |
| `/lexicon link` | Link this Discord server to an EQ2 world + guild. |
| `/lexicon voice` | Set or clear the raid voice channel used for voice attendance. |
| `/lexicon parses` | Set or clear the channel where new raid kills are posted. |
| `/lexicon status` | Show the current link. |
| `/lexicon unlink` | Remove the link. |

The group is gated on Discord's own **Manage Server** permission:
`default_permissions` hides it from regular members, and `interaction_check`
enforces it server-side (server admins can loosen the UI default per
integration; the check is the hard floor). There is deliberately no
site-officer verification: the Discord server's admin is the right authority
for what their server maps to.

## Parse poster (`backend/bot/cogs/parse_posts.py`)

Every minute, for each link with a parses channel, the cog posts newly uploaded
raid boss **kills** (wipes never post) for the linked guild: outcome and
duration, raid-wide DPS/HPS, top-5 DPS and HPS side by side, and a link to the
parse page. Only raid-sized fights (`MIN_PLAYERS = 7`, the mirror-grouping raid
threshold) qualify, and at most `MAX_POSTS_PER_TICK` go out per link per tick.

### Dedup model

Several raiders upload the same fight ("mirrors"), and uploads can arrive long
after the fight (late logs, EQ2Parser history replays). The rules that keep one
fight to one post:

1. **Settle.** A fight is considered only once its uploads have had `SETTLE_S`
   (3 minutes) to arrive; the poll window ends at `now - SETTLE_S`.
2. **Group by fight time, not upload time.** For each candidate upload, every
   earlier upload of a fight that *started* within `REGROUP_MARGIN_S` (1 hour)
   is loaded and the set is mirror-grouped with the same `_group_into_fights`
   the parses list uses (mirrors chain within `PARSE_MIRROR_WINDOW_S` of each
   other). A straggler therefore attaches to its already-posted fight however
   late it turns up.
3. **Earliest upload decides.** A grouped fight posts only if its *earliest*
   upload is past the link's watermark. A late mirror joins a fight whose
   earliest upload is before the watermark, so it is skipped.
4. **Age cap.** A fight older than `MAX_FIGHT_AGE_S` (24 hours) never posts, so
   a replay or re-import cannot re-announce a past raid.
5. **Watermark.** After a tick the link's watermark advances to
   `now - SETTLE_S`, so a restart never re-posts.

## Voice attendance poller (`backend/bot/cogs/voice_attendance.py`)

A cross-check for raid attendance. Every 120 seconds, for each link with a
voice channel configured:

1. Ask the attendance store whether the linked guild has a **live** session
   (`attendance_store.find_live_session(world, guild, now)` — a session the
   parser's uploads would merge into right now).
2. If so, snapshot the members connected to the voice channel and record
   `kind='voice'` observations via `record_voice`. For voice rows the
   `character_name` column carries the Discord user id.

The site's per-player attendance rollup then shows a headset marker and flags
players who were "in voice, not in game".

The idle path costs one registry query per tick plus one indexed session probe
per configured guild: no Discord API calls and no writes unless a session is
live. Resolving voice states to members needs the privileged **Server Members**
intent, which must be enabled in the Discord developer portal or login fails.
