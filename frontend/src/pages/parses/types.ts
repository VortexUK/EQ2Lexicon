/**
 * Shared types for the parses page family.
 *
 * `ParseEncounterSummary` / `ParseUploadSummary` / `ParsePermissions` /
 * `ParsesListResponse` mirror the FastAPI Pydantic models in
 * web/routes/parses/models.py — keep them in sync.
 *
 * `Category` / `ZoneDayBucket` / `GuildBucket` are frontend-only shapes
 * produced by groupEncounters() in ParsesPage.tsx and consumed by
 * GuildSection / CategorySection / ZoneDaySection / FightRow.
 */

export interface ParsePermissions {
  can_delete: boolean
}

export interface ParseUploadSummary {
  id: number
  uploaded_by: string                       // EQ2 character name (logger_name)
  uploader_discord_id: string | null        // resolved from source_dsn ('plugin:<id>')
  uploader_display_name: string | null      // joined from users.discord_name
  started_at: number
  duration_s: number
  total_damage: number
  encdps: number
  success_level: number
  permissions: ParsePermissions
}

export interface ParseEncounterSummary {
  id: number
  act_encid: string
  title: string
  zone: string | null
  started_at: number       // unix seconds, UTC
  ended_at: number
  duration_s: number
  total_damage: number
  encdps: number
  kills: number
  deaths: number
  success_level: number      // ACT enum: 0=unknown, 1=win, 2=loss, 3=mixed
  combatant_count: number
  player_count: number
  // Backend-computed Raid / Dungeon / Other bucket (see
  // web/routes/parses/list.py:_classify_zone). Drives the Guild → Category
  // hierarchy on this page.
  category: 'raid' | 'dungeon' | 'other'
  uploaded_by: string                       // canonical upload's character name
  uploader_discord_id: string | null        // canonical upload's Discord ID
  uploader_display_name: string | null      // canonical upload's Discord display name
  guild_name: string | null   // stamped at ingest from uploader's Census guild
  permissions: ParsePermissions
  // Server-side mirror grouping (B2.15e) — every raider's upload for this
  // fight, including the canonical (single-upload fights have length 1).
  uploads: ParseUploadSummary[]
}

export interface ParsesListResponse {
  results: ParseEncounterSummary[]
  total: number
  // Pagination cursor — pass back as ?before= for the window below this page.
  // null/absent when the page reaches the end of the data.
  next_before?: number | null
}

export type Category = 'raid' | 'dungeon' | 'other'

export interface ZoneDayBucket {
  key: string                          // "2026-05-24 · Castle Mistmoore"
  date: string                         // local YYYY-MM-DD
  zone: string                         // "Castle Mistmoore" or "(unknown zone)"
  fights: ParseEncounterSummary[]      // sorted started_at desc within the bucket
}

export interface GuildBucket {
  guild: string                                // "Exordium" or NO_GUILD
  fightsByCategory: Record<Category, ZoneDayBucket[]>  // each category's zone-day buckets, newest bucket first
  totalFights: number
}

/**
 * "No guild" sentinel. Used as a guild-name placeholder in GuildBucket
 * for fights whose uploader had no Census-resolved guild (e.g. the
 * uploader's character isn't currently in a guild, or the guild lookup
 * failed at ingest). Centralised here so the two consumers
 * (ParsesPage.tsx for the groupEncounters sort, GuildSection.tsx for
 * the trash-button filter predicate) read from one source of truth.
 */
export const NO_GUILD = 'No Guild'
