/**
 * characterSheet — shared character-sheet data model + display config.
 *
 * Extracted from CharacterPage so the compare page reuses the exact same
 * types, stat grouping, slot layout, and tier styling — a single source of
 * truth that can't drift between the two (the same lesson as the backend's
 * character_upgradeable_spells consolidation).
 */

// ── Types (mirror backend CharacterResponse) ─────────────────────────────────

export interface AdornSlot {
  color: string
  adorn_name: string | null
  adorn_id: string | null
  ilvl_bonus: number
}

export interface EquipmentSlot {
  slot: string
  name: string
  item_id: string | null
  icon_id: string | null
  tier: string | null
  adorn_slots: AdornSlot[]
}

export interface CharacterStats {
  health_max: number | null
  health_regen: number | null
  power_max: number | null
  power_regen: number | null
  run_speed: number | null
  status_points: number | null
  str_eff: number | null
  sta_eff: number | null
  agi_eff: number | null
  wis_eff: number | null
  int_eff: number | null
  armor: number | null
  avoidance: number | null
  block_chance: number | null
  parry: number | null
  mit_physical: number | null
  mit_elemental: number | null
  mit_noxious: number | null
  mit_arcane: number | null
  potency: number | null
  crit_chance: number | null
  crit_bonus: number | null
  fervor: number | null
  dps: number | null
  double_attack: number | null
  ability_doublecast: number | null
  attack_speed: number | null
  strikethrough: number | null
  accuracy: number | null
  ability_mod: number | null
  weapon_damage_bonus: number | null
  flurry: number | null
  lethality: number | null
  toughness: number | null
  reuse_speed: number | null
  casting_speed: number | null
  recovery_speed: number | null
  primary_min: number | null
  primary_max: number | null
  primary_delay: number | null
  secondary_min: number | null
  secondary_max: number | null
  secondary_delay: number | null
  ranged_min: number | null
  ranged_max: number | null
  ranged_delay: number | null
}

export interface Character {
  id: string
  name: string
  level: number | null
  cls: string | null
  race: string | null
  gender: string | null
  deity: string | null
  aa_count: number
  world: string
  ts_class: string | null
  ts_level: number | null
  guild_name: string | null
  ilvl: number | null
  stats: CharacterStats
  equipment: EquipmentSlot[]
  fetched_at?: number | null
  stale?: boolean
}

// ── Stat display formats ─────────────────────────────────────────────────────

export type Fmt = 'int' | 'pct' | 'pct1' | 'dec1'

export function fmtStat(value: number, format?: Fmt): string {
  switch (format) {
    case 'int':  return value.toLocaleString()
    case 'pct':  return `${Math.round(value)}%`
    case 'pct1': return `${value.toFixed(1)}%`
    case 'dec1': return value.toFixed(1)
    default:     return String(value)
  }
}

// ── Stat groups — the single source of truth for stat grouping/labels ────────
// Transcribed from CharacterPage's StatsPanel; both the character sheet and the
// compare page render from THIS table. The Weapon group is composite
// (min–max ranges + delay) and lives in WEAPON_SLOTS below.

export interface StatRowDef { key: keyof CharacterStats; label: string; fmt: Fmt }
export interface StatGroupDef { title: string; rows: StatRowDef[] }

export const STAT_GROUPS: StatGroupDef[] = [
  {
    title: 'Attributes',
    rows: [
      { key: 'str_eff', label: 'Strength',     fmt: 'int' },
      { key: 'sta_eff', label: 'Stamina',      fmt: 'int' },
      { key: 'agi_eff', label: 'Agility',      fmt: 'int' },
      { key: 'wis_eff', label: 'Wisdom',       fmt: 'int' },
      { key: 'int_eff', label: 'Intelligence', fmt: 'int' },
    ],
  },
  {
    title: 'Defense',
    rows: [
      { key: 'armor',         label: 'Armor',         fmt: 'int' },
      { key: 'avoidance',     label: 'Avoidance',     fmt: 'int' },
      { key: 'block_chance',  label: 'Block Chance',  fmt: 'pct1' },
      { key: 'parry',         label: 'Parry',         fmt: 'int' },
      { key: 'mit_physical',  label: 'Physical Mit',  fmt: 'pct1' },
      { key: 'mit_elemental', label: 'Elemental Mit', fmt: 'pct1' },
      { key: 'mit_noxious',   label: 'Noxious Mit',   fmt: 'pct1' },
      { key: 'mit_arcane',    label: 'Arcane Mit',    fmt: 'pct1' },
    ],
  },
  {
    title: 'Combat',
    rows: [
      { key: 'potency',             label: 'Potency',            fmt: 'dec1' },
      { key: 'crit_chance',         label: 'Crit Chance',        fmt: 'pct1' },
      { key: 'crit_bonus',          label: 'Crit Bonus',         fmt: 'pct1' },
      { key: 'fervor',              label: 'Fervor',             fmt: 'dec1' },
      { key: 'dps',                 label: 'DPS',                fmt: 'dec1' },
      { key: 'double_attack',       label: 'Multi Attack',       fmt: 'pct1' },
      { key: 'ability_doublecast',  label: 'Ability Doublecast', fmt: 'pct1' },
      { key: 'attack_speed',        label: 'Attack Speed',       fmt: 'pct1' },
      { key: 'ability_mod',         label: 'Ability Mod',        fmt: 'int' },
      { key: 'weapon_damage_bonus', label: 'Weapon Damage',      fmt: 'pct1' },
      { key: 'flurry',              label: 'Flurry',             fmt: 'pct1' },
      { key: 'strikethrough',       label: 'Strikethrough',      fmt: 'pct1' },
      { key: 'accuracy',            label: 'Accuracy',           fmt: 'pct1' },
      { key: 'lethality',           label: 'Lethality',          fmt: 'pct1' },
      { key: 'toughness',           label: 'Toughness',          fmt: 'dec1' },
    ],
  },
  {
    title: 'Casting',
    rows: [
      { key: 'reuse_speed',    label: 'Reuse Speed',    fmt: 'pct1' },
      { key: 'casting_speed',  label: 'Casting Speed',  fmt: 'pct1' },
      { key: 'recovery_speed', label: 'Recovery Speed', fmt: 'pct1' },
    ],
  },
]

/** Composite weapon rows: min–max damage range + delay per weapon slot. */
export interface WeaponSlotDef {
  label: string
  min: keyof CharacterStats
  max: keyof CharacterStats
  delay: keyof CharacterStats
}

export const WEAPON_SLOTS: WeaponSlotDef[] = [
  { label: 'Primary',   min: 'primary_min',   max: 'primary_max',   delay: 'primary_delay' },
  { label: 'Secondary', min: 'secondary_min', max: 'secondary_max', delay: 'secondary_delay' },
  { label: 'Ranged',    min: 'ranged_min',    max: 'ranged_max',    delay: 'ranged_delay' },
]

// ── Paperdoll slot layout ────────────────────────────────────────────────────
// [display label, canonical slot key] in in-game paperdoll order.

export const LEFT_SLOTS: [string, string][] = [
  ['Charm',      'activate1'],
  ['Cloak',      'cloak'],
  ['Head',       'head'],
  ['Shoulders',  'shoulders'],
  ['Chest',      'chest'],
  ['Arms',       'forearms'],
  ['Hands',      'hands'],
  ['Legs',       'legs'],
  ['Feet',       'feet'],
  ['Primary',    'primary'],
  ['Secondary',  'secondary'],
]

export const RIGHT_SLOTS: [string, string][] = [
  ['Charm',      'activate2'],
  ['Ear',        'ears'],
  ['Ear',        'ears2'],
  ['Neck',       'neck'],
  ['Ring',       'left_ring'],
  ['Ring',       'right_ring'],
  ['Wrist',      'left_wrist'],
  ['Wrist',      'right_wrist'],
  ['Waist',      'waist'],
  ['Ranged',     'ranged'],
]

export const CONSUMABLE_SLOTS: [string, string][] = [
  ['Food',  'food'],
  ['Drink', 'drink'],
]

const DISPLAY_TO_BASE: Record<string, string> = {
  Primary: 'primary', Secondary: 'secondary', Ranged: 'ranged',
  Head: 'head', Chest: 'chest', Shoulders: 'shoulders',
  Forearms: 'forearms', Hands: 'hands', Legs: 'legs',
  Feet: 'feet', Waist: 'waist', Neck: 'neck', Cloak: 'cloak',
  Charm: 'activate', Finger: 'ring', Ear: 'ear', Wrist: 'wrist',
  Food: 'food', Drink: 'drink',
}
const MULTI_SUFFIXES: Record<string, string[]> = {
  activate: ['activate1', 'activate2'],
  ring:     ['left_ring', 'right_ring'],
  ear:      ['ears', 'ears2'],
  wrist:    ['left_wrist', 'right_wrist'],
}

/** Map a character's equipment list onto canonical slot keys (two rings/ears/
 * wrists disambiguated positionally). */
export function buildSlotMap(equipment: EquipmentSlot[]): Map<string, EquipmentSlot> {
  const map = new Map<string, EquipmentSlot>()
  const counters: Record<string, number> = {}
  for (const s of equipment) {
    const base = DISPLAY_TO_BASE[s.slot]
    if (!base) continue
    const suffixes = MULTI_SUFFIXES[base]
    let key: string
    if (suffixes) {
      counters[base] = (counters[base] ?? 0) + 1
      key = suffixes[counters[base] - 1] ?? base
    } else {
      key = base
    }
    map.set(key, s)
  }
  return map
}

// ── Item tier (rarity) styling — in-game glow treatment ─────────────────────

export type TierStyle = { color: string; textShadow?: string }

const OUTLINE = '-1px 0px 0px #000, 0px 1px 0px #000, 1px 0px 0px #000, 0px -1px 0px #000'

// Colours reference the --rarity-* tokens; the glows stay literal.
const TIER_STYLE: Record<string, TierStyle> = {
  MYTHICAL: {
    color: 'var(--rarity-mythical)',
    textShadow: `${OUTLINE}, 0px 0px 4px #C859E6, 0px 0px 4px #C859E6`,
  },
  FABLED: {
    color: 'var(--rarity-fabled)',
    textShadow: `${OUTLINE}, 0px 0px 4px #DF535F, 0px 0px 4px #DF535F`,
  },
  LEGENDARY: {
    color: 'var(--rarity-legendary)',
    textShadow: `${OUTLINE}, 0px 0px 4px #D56900, 0px 0px 4px #ffc993`,
  },
  MASTERCRAFTED: {
    color: 'var(--rarity-treasured)',
    textShadow: `${OUTLINE}, 0px 0px 4px #D56900, 0px 0px 4px #92d7fd`,
  },
  TREASURED: {   // same as mastercrafted
    color: 'var(--rarity-treasured)',
    textShadow: `${OUTLINE}, 0px 0px 4px #D56900, 0px 0px 4px #92d7fd`,
  },
  UNCOMMON: { color: 'var(--rarity-handcrafted)' },
  COMMON:   { color: 'var(--text)' },
}

export function tierStyle(tier: string | null): TierStyle {
  const key = (tier ?? '').toUpperCase()
  if (TIER_STYLE[key]) return TIER_STYLE[key]
  // Compound tier like "MASTERCRAFTED FABLED" — use the last recognised word
  const words = key.split(/\s+/)
  for (let i = words.length - 1; i >= 0; i--) {
    if (TIER_STYLE[words[i]]) return TIER_STYLE[words[i]]
  }
  return { color: 'var(--text)' }
}
