import type { Character } from '../characterSheet'

/** One picker side's resolution state. Sides load independently. */
export type SideState =
  | { status: 'empty' }
  | { status: 'loading'; name: string }
  | { status: 'ok'; char: Character }
  | { status: 'not_found'; name: string }
  | { status: 'census_unavailable'; name: string }
  | { status: 'error'; name: string; message: string }

export interface FavoriteEntry {
  character_name: string
  world: string
  created_at: number
  level: number | null
  cls: string | null
  ts_class: string | null
  ts_level: number | null
  guild_name: string | null
}

export type CompareTab = 'stats' | 'gear' | 'aas'
