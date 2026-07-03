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

export type Tab = 'roster' | 'spells' | 'adorns' | 'raids' | 'claims' | 'watch'

// Shared table-cell utility classes (invariant, dynamic bits stay inline at call sites)
export const TH_CLS = 'px-2.5 py-2 text-[0.72rem] uppercase tracking-[0.05em] font-semibold whitespace-nowrap'
export const TD_CLS = 'px-2.5 py-1.5 text-[0.88rem] whitespace-nowrap'
