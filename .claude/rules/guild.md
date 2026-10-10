---
paths:
  - "backend/server/api/guild*.py"
  - "backend/server/api/item_watch.py"
  - "backend/server/api/raid_schedule.py"
  - "backend/server/raid_live.py"
  - "backend/server/recruitment_sweep.py"
  - "backend/server/db/guild_*"
  - "backend/server/db/raid_schedule.*"
  - "backend/server/core/twitch.py"
  - "backend/server/core/text_moderation.py"
  - "frontend/src/pages/guild/**"
---

# Guild pages: roster, officers, settings, recruitment, raid schedule

## Architecture notes

- **Circular import avoidance**: `_overview_to_char_response` in `backend/server/guild_cache.py` uses a local import of `_build_char_response` from the `backend/server/api/character/` package inside the function body.
- **Route split**: Large guild.py split into `guild.py` (roster + spellcheck + adorn), `guild_officer.py` (officer claim review), `item_watch.py` (item watch).

## Files

| File | Purpose |
|---|---|
| `backend/server/api/guild_officer.py` | Officer claim-review endpoints; imports _officer_chars, _roster_rank_map from guild.py |
| `backend/server/api/item_watch.py` | Item watch endpoints; imports _officer_chars, _roster_rank_map from guild.py |
| `backend/server/api/guild_settings.py` | Per-guild switches, LEADER-or-admin write (Census rank_id 0 — `_leader_chars` / `_leader_chars_cached` in guild.py, the first place rank 0 is distinguished from `_OFFICER_RANKS`), public `GET /api/guild/{name}/settings`, `PUT` audited as `guild_settings_updated`. Store `backend/server/db/guild_settings.py` (`guild_settings` table in the users schema, one boolean column per switch, absent row == defaults). Switches: `officers_can_delete_parses` (default on) — enforced in `parses/delete.py::_can_delete_encounter` (memoised per guild per request) and `parses/list.py::_compute_permissions` (one IN-query, officers only); gates ONLY officer parse deletion — uploaders keep their own uploads, admins unaffected; and `officer_rank_ids integer[]` (migration 0023, NULL = site default `OFFICER_RANK_IDS` {0,1}) — which Census rank ids count as officers for the guild, since some guilds run three leadership ranks. Rank 0 (leader) is always included server-side. Every officer check reads it: `guild.officer_rank_ids(guild)` (60 s `TTLCache`, invalidated by the PUT), the /parses permission pass, the notification poll and the bot's `/lexicon link` gate (`officer_rank_ids_for` bulk read). The Settings tab lists the ranks found on the roster (`GuildPage` derives `rosterRanks`) as checkboxes, leader locked on, plus a reset to the site default. `officer-status` also returns `is_leader`; the guild page shows a Settings tab to leader/admin. Added after 2026-09-27. |
| `backend/server/api/guild_recruitment.py` | Guild recruitment API — public read, officer-or-admin write (raid_schedule's `_officer_chars` gate). Profiles keyed by CENSUS GUILD ID in the store (`backend/server/db/guild_recruitment.py`, users schema) so a profile + logo survive guild renames; routes stay name-addressed and every officer save re-resolves the id + refreshes the stored name. Fields: recruiting flag, blocklist-screened description (≤1000), needed classes (validated vs the classes schema adventure names), curated tags (`RECRUITMENT_TAGS`), ≤3 roster-validated in-game contacts, `DISCORD_INVITE_RE` link. Logo: base64 JSON upload → PIL re-encode (`_process_logo`: format+dims header-checked pre-decode, exif_transpose, ≤200px WebP q85, ≤128KB) stored as bytea in the users schema (inside the Postgres backups); served with ETag/304 + nosniff; upload/removal audited with the actor (`guild_logo_uploaded`/`guild_logo_removed` — the abuse trail). `GET /api/recruiting` lists recruiting guilds with `member_count` AND `account_count` via `census_store.latest_guild_counts` (guild_history — accounts is the honest size of an alt-heavy guild; the card shows "80 accounts · 208 characters" then "updated 2h ago") plus each guild's public `raid_teams` (`raid_schedule_db.get_schedules`, two statements for the whole list; the card renders "Raids Tue, Thu 20:00–23:00 (Europe/London)" linking to the guild's `?tab=raids`). Daily sweep `backend/server/recruitment_sweep.py` (app lifespan): census lookup BY ID per listed guild — renamed → stored name refreshed, gone → auto-delisted (`recruiting=0`, never deleted), census error → untouched. Erasure tombstones `updated_by`/`logo_uploaded_by`; the logo blob stays (guild asset). Frontend: guild-page Recruitment tab (`pages/guild/GuildRecruitmentTab.tsx`, view for everyone + officer edit) and browse page `pages/RecruitingPage.tsx` at `/recruiting` (nav in BOTH App.tsx BROWSE_ITEMS and MobileNav GROUPS). |
| `backend/server/api/raid_schedule.py` | Guild raid-schedule API. Public `GET /api/guild/{name}/raid-schedule`; officer-or-admin `PUT` (full replace, ≤4 teams × ≤4 raids, each ≤5h, IANA tz, Twitch validated, free text blocklist-screened). Clearing == PUT with empty `teams`. `GET /api/raiding-live` returns the current world's live teams. Tables `raid_teams`/`raid_slots` in the users schema (`backend/server/db/raid_schedule.py`; `raid_slots.days` is `integer[]`). |
| `backend/server/raid_live.py` | Twitch-verified "Raiding live" poller (`poll_loop`, `app.py` lifespan). Finds teams inside a scheduled window (team tz, ±15min grace) then verifies live via Twitch Helix; caches per world for `/api/raiding-live`. No-ops without `TWITCH_CLIENT_ID/SECRET`. |
| `backend/server/core/twitch.py` | `parse_twitch_login` (accept only `twitch.tv/<channel>`) + `is_blocked` (thin wrapper over `text_moderation`). |
| `backend/server/core/text_moderation.py` | Shared profanity screen + input sanitiser for officer free text (raid team names/labels) and Twitch logins. `contains_blocked_term` normalises (NFKC, strip invisible/bidi/control chars) then screens via the `better-profanity` package (maintained wordlist + leetspeak variants — no slur list committed in-repo); word-based, so no substring false positives. `sanitize_text` cleans + caps length. Hit → reject + `audit_log`. |
