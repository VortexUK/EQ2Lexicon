---
paths:
  - "backend/bot/**"
---

# Discord bot

## Files

| File | Purpose |
|---|---|
| `backend/bot/bot.py` | Registers all cogs, syncs slash commands to three specific guild IDs (648253204760625160, 955890381847928892, 1502314690041221260) for instant propagation plus a global sync. Also: shared app-command error handler, tracked background-task set cancelled in close() (the bot races the web lifespan, but pre-lifespan queries fall back to direct psycopg connections and guild_context degrades on psycopg.Error — no defensive init needed), and `intents.members = True` — the SERVER MEMBERS privileged intent MUST be toggled in the dev portal or login fails (main.py catches it, logs CRITICAL, web half keeps running). |
| `backend/bot/guild_context.py` | Per-Discord-guild context: `resolve_guild_context(discord_guild_id)` reads the `discord_guild_links` registry (users schema, edited via `/lexicon` link/voice/status/unlink, manage_guild gated) → GuildContext(world, guild_name, voice_channel_id, linked). Unlinked/DM → `FALLBACK_WORLD = "Wuoshi"` (hardcoded by design). All world-aware cogs resolve here — the env WORLD pin is bot-dead. |
| `backend/bot/render.py` + `messaging.py` | Pure text half of bot responses (table builders, `plan_code_block` → SendPlan for the 2000-char wrap-vs-file decision; fully unit-tested in tests/bot/) + the one discord I/O sender `send_plan`. Cogs are thin adapters. |
| `backend/bot/cogs/voice_attendance.py` | Phase 3 voice cross-check: tasks.loop every 120s → for each `/lexicon voice`-configured guild, if `attendance_store.find_live_session(world, guild, now)` (merge-gap window) → snapshot the voice channel members → `record_voice` kind='voice' observations (character_name carries the discord id). Site rollup shows 🎧 + "in voice, not in game" on AWOL. Idle path = 1 SQL per linked guild, no Discord API calls. |
| `backend/bot/cogs/items.py` | `/item` — accepts name, numeric ID, or game link |
| `backend/bot/cogs/guild.py` | `/guild` — tabular member list sorted by rank then level |
| `backend/bot/cogs/spellcheck.py` | `/spellcheck` — spell tier summary or full list (`details:True`) |
| `backend/bot/cogs/aacheck.py` | `/aacheck` — renders a character's AA tree with tier badges |
