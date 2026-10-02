// Shared types for GuildPage and its sub-tab components.

export interface GuildMember {
  name: string
  level: number | null
  cls: string | null
  ts_class: string | null
  ts_level: number | null
  aa_level: number | null
  ilvl: number | null
  deity: string | null
  rank: string | null
  rank_id: number | null
  guild_status: number | null
}

export interface GuildData {
  name: string
  world: string
  members: GuildMember[]
  fetched_at?: number | null
  stale?: boolean
}

export interface MemberSpellTiers {
  name: string
  rank: string | null
  rank_id: number | null
  tiers: Record<string, number>
  total: number
  spell_names: Record<string, string[]>
}

export interface GuildSpellCheck {
  guild_name: string
  world: string
  tiers: string[]
  members: MemberSpellTiers[]
}

export interface AdornColorStats {
  filled: number
  total: number
}

export interface MemberAdornStats {
  name: string
  rank: string | null
  rank_id: number | null
  adorns: Record<string, AdornColorStats>
  missing: Record<string, string[]>
}

export interface GuildAdornCheck {
  guild_name: string
  world: string
  colors: string[]
  members: MemberAdornStats[]
}

export type Tab =
  | 'roster'
  | 'spells'
  | 'adorns'
  | 'progression'
  | 'history'
  | 'raids'
  | 'recruitment'
  | 'attendance'
  | 'claims'
  | 'watch'
  | 'settings'

/** One row of GET /api/guild/{name}/history — a UTC day's capture. */
export interface GuildHistoryPoint {
  day: string
  captured_at: number
  level: number | null
  members: number | null
  accounts: number | null
  achievement_count: number | null
  max_level_members: number | null
  distinct_classes: number | null
}

export interface GuildHistoryResponse {
  guild: string
  world: string
  days: number
  points: GuildHistoryPoint[]
}

export interface GuildSettings {
  officers_can_delete_parses: boolean
  updated_at: number | null
  updated_by_name: string | null
}

/** GET/PUT /api/guild/{name}/recruitment — the officer-editable profile. */
export interface RecruitmentProfile {
  recruiting: boolean
  description: string
  classes: string[]
  tags: string[]
  contacts: string[]
  discord_url: string | null
  updated_at: number | null
  updated_by_name: string | null
  has_logo: boolean
  logo_uploaded_at: number | null
  logo_uploaded_by_name: string | null
  /** The curated tag slugs — single source of truth is the backend. */
  available_tags: string[]
}

// Shared table-cell utility classes (invariant, dynamic bits stay inline at call sites)
export const TH_CLS = 'px-2.5 py-2 text-[0.72rem] uppercase tracking-[0.05em] font-semibold whitespace-nowrap'
export const TD_CLS = 'px-2.5 py-1.5 text-[0.88rem] whitespace-nowrap'
