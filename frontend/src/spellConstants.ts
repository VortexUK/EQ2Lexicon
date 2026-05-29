// Shared spell types, constants and module-level caches.
// Kept in a separate .ts file so CharacterSpellsTab.tsx exports only
// React components — required for Vite Fast Refresh to work correctly.

// ── Types ─────────────────────────────────────────────────────────────────────

export interface SpellEntry {
  name:          string
  tier:          string
  level:         number
  spell_type:    string
  icon_id:       number | null
  icon_backdrop: number | null
}

export interface CharacterSpellsData {
  character_name: string
  spells:         SpellEntry[]
  tier_counts:    Record<string, number>
  tiers_present:  string[]
}

export interface Ingredient {
  name:        string
  quantity:    number
  category:    'primary' | 'secondary' | 'fuel'
  item_id:     number | null
  icon_id:     number | null
  tier:        string | null
  description: string | null
  item_level:  number | null
}

export interface UpgradeMaterialsData {
  spells_needing_upgrade: number
  spells_with_recipe:     number
  ingredients:            Ingredient[]
}

// ── Module-level caches ───────────────────────────────────────────────────────
// Survive re-renders and Vite HMR remounts. Keyed by lower-cased character name.

export const spellsCache    = new Map<string, CharacterSpellsData>()
export const materialsCache = new Map<string, UpgradeMaterialsData>()

// ── Constants ─────────────────────────────────────────────────────────────────

export const SPELL_TIER_ORDER = [
  'Apprentice', 'Journeyman', 'Adept', 'Expert', 'Master', 'Grandmaster',
]

export const SPELL_TIER_ICON: Record<string, string> = {
  Apprentice:  'spell_app',
  Journeyman:  'spell_jour',
  Adept:       'spell_ad',
  Expert:      'spell_exp',
  Master:      'spell_m',
  Grandmaster: 'spell_gm',
}

export const SPELL_TIER_COLOURS: Record<string, { text: string; bg: string }> = {
  Apprentice:  { text: '#ef4444', bg: 'rgba(239,68,68,0.12)'   },
  Journeyman:  { text: '#f97316', bg: 'rgba(249,115,22,0.12)'  },
  Adept:       { text: '#eab308', bg: 'rgba(234,179,8,0.12)'   },
  Expert:      { text: '#84cc16', bg: 'rgba(132,204,22,0.12)'  },
  Master:      { text: '#22c55e', bg: 'rgba(34,197,94,0.12)'   },
  Grandmaster: { text: '#10b981', bg: 'rgba(16,185,129,0.15)'  },
}
