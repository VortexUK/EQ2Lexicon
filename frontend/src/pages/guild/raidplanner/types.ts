// Shared types for the raid planner (pages/guild/raidplanner/).

export interface RosterEntry {
  name: string
  cls: string | null
  level: number | null
  role: 'raider' | 'raid_alt' | null
  rank: string | null
  rank_id: number | null
}

export interface Placement {
  character_name: string
  group_num: number | null // 1..4
  slot: number | null // 0..5
  sitout: boolean
}

export interface PlannerData {
  is_officer: boolean
  team_index: number
  team_count: number
  date: string
  roster: RosterEntry[]
  placements: Placement[]
  availability: Record<string, 'tentative' | 'afk'> // char name (lower) → status
  players: Record<string, string> // char name (lower) → player display name
}

/** Where a character chip can be dropped. */
export type DropTarget =
  | { kind: 'slot'; group: number; slot: number }
  | { kind: 'sitout' }
  | { kind: 'bench' }

export const GROUPS = 4
export const SLOTS_PER_GROUP = 6

export const AVAILABILITY_LABEL: Record<string, string> = {
  afk: 'AFK',
  tentative: 'Tentative',
}
