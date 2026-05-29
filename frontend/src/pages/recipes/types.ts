// ── Shared types, constants, and helpers for the Recipes feature ──────────────

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Ingredient {
  description: string
  quantity: number
}

export interface RecipeResult {
  id: number
  name: string
  bench: string | null
  bench_label: string | null
  craft_tier: string | null      // T1 … T14
  crafted_tier: string | null    // spell-scroll quality (Expert, etc.)
  primary_comp: string | null
  primary_qty: number | null
  secondary_comps: Ingredient[]
  fuel_comp: string | null
  fuel_qty: number | null
  out_formed_id: number | null
  out_formed_count: number | null
  class_label: string | null
  craft_classes: string[]        // tradeskill classes that can make it
}

export interface RecipeSearchResponse {
  results: RecipeResult[]
  total: number
  page: number
  per_page: number
}

export interface ShoppingEntry {
  recipeId: number
  recipeName: string
  qty: number              // number of crafting runs
  primary_comp: string | null
  primary_qty: number | null
  secondary_comps: Ingredient[]
  fuel_comp: string | null
  fuel_qty: number | null
}

export interface IngredientSummary {
  regular: { name: string; total: number }[]
  fuel:    { name: string; total: number }[]
}

// ── Constants ─────────────────────────────────────────────────────────────────

export const STORAGE_KEY = 'eq2-shopping-list'

// T1 = levels 1-9, T2 = 10-19, … derived from the fuel component prefix
export const CRAFT_TIERS = [
  'T1','T2','T3','T4','T5','T6','T7','T8','T9','T10','T11','T12','T13','T14',
]

export const CRAFT_TIER_LABELS: Record<string, string> = {
  T1: 'T1  (1–9)',   T2: 'T2  (10–19)', T3: 'T3  (20–29)',
  T4: 'T4  (30–39)', T5: 'T5  (40–49)', T6: 'T6  (50–59)',
  T7: 'T7  (60–69)', T8: 'T8  (70–79)', T9: 'T9  (80–89)',
  T10:'T10 (90–99)', T11:'T11 (100+)',   T12:'T12',
  T13:'T13',         T14:'T14',
}

// Tradeskill class filter
export const PRIMARY_CRAFT_CLASSES = [
  'Alchemist', 'Armorer', 'Carpenter', 'Jeweler', 'Provisioner',
  'Sage', 'Tailor', 'Weaponsmith', 'Woodworker',
]
export const SECONDARY_CRAFT_CLASSES = ['Adorner', 'Tinkerer']

export const CLASS_OPTIONS: { label: string; value: string }[] = [
  { label: 'All Classes',    value: '' },
  { label: '── Fighter ──',  value: '__hdr' },
  { label: '  Guardian',     value: 'guardian' },
  { label: '  Berserker',    value: 'berserker' },
  { label: '  Monk',         value: 'monk' },
  { label: '  Bruiser',      value: 'bruiser' },
  { label: '  Shadowknight', value: 'shadowknight' },
  { label: '  Paladin',      value: 'paladin' },
  { label: '── Priest ──',   value: '__hdr' },
  { label: '  Templar',      value: 'templar' },
  { label: '  Inquisitor',   value: 'inquisitor' },
  { label: '  Warden',       value: 'warden' },
  { label: '  Fury',         value: 'fury' },
  { label: '  Mystic',       value: 'mystic' },
  { label: '  Defiler',      value: 'defiler' },
  { label: '  Channeler',    value: 'channeler' },
  { label: '── Mage ──',     value: '__hdr' },
  { label: '  Wizard',       value: 'wizard' },
  { label: '  Warlock',      value: 'warlock' },
  { label: '  Illusionist',  value: 'illusionist' },
  { label: '  Coercer',      value: 'coercer' },
  { label: '  Conjuror',     value: 'conjuror' },
  { label: '  Necromancer',  value: 'necromancer' },
  { label: '── Scout ──',    value: '__hdr' },
  { label: '  Swashbuckler', value: 'swashbuckler' },
  { label: '  Brigand',      value: 'brigand' },
  { label: '  Troubador',    value: 'troubador' },
  { label: '  Dirge',        value: 'dirge' },
  { label: '  Ranger',       value: 'ranger' },
  { label: '  Assassin',     value: 'assassin' },
  { label: '  Beastlord',    value: 'beastlord' },
]

export const CTRL_CLS = 'bg-surface border border-border rounded-sm2 text-text text-[0.88rem] py-1.5 px-2.5 outline-none w-full box-border'

// ── XML download ──────────────────────────────────────────────────────────────

function xmlEsc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function downloadShoppingListXml(list: ShoppingEntry[], summary: IngredientSummary): void {
  const lines: string[] = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<shoppinglist>',
    '  <spells>',
    ...list.map(e => `    <spell Name="${xmlEsc(e.recipeName)}">${e.qty}</spell>`),
    '  </spells>',
    '  <materials>',
    ...summary.regular.map(m => `    <material Name="${xmlEsc(m.name)}">${m.total}</material>`),
    '  </materials>',
    '  <fuels>',
    ...summary.fuel.map(f => `    <fuel Name="${xmlEsc(f.name)}">${f.total}</fuel>`),
    '  </fuels>',
    '</shoppinglist>',
  ]
  const blob = new Blob([lines.join('\n')], { type: 'application/xml' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = 'shopping-list.xml'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// ── localStorage helpers ───────────────────────────────────────────────────────

export function loadList(): ShoppingEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

export function saveList(list: ShoppingEntry[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list))
  } catch { /* storage full / private browsing */ }
}

// ── Ingredient aggregation ────────────────────────────────────────────────────

export function aggregateList(list: ShoppingEntry[]): IngredientSummary {
  const regular: Record<string, number> = {}
  const fuel:    Record<string, number> = {}

  for (const entry of list) {
    const n = entry.qty
    if (entry.primary_comp && entry.primary_qty) {
      regular[entry.primary_comp] = (regular[entry.primary_comp] ?? 0) + entry.primary_qty * n
    }
    for (const sc of entry.secondary_comps) {
      if (sc.description && sc.quantity) {
        regular[sc.description] = (regular[sc.description] ?? 0) + sc.quantity * n
      }
    }
    if (entry.fuel_comp && entry.fuel_qty) {
      fuel[entry.fuel_comp] = (fuel[entry.fuel_comp] ?? 0) + entry.fuel_qty * n
    }
  }

  return {
    regular: Object.entries(regular)
      .map(([name, total]) => ({ name, total }))
      .sort((a, b) => a.name.localeCompare(b.name)),
    fuel: Object.entries(fuel)
      .map(([name, total]) => ({ name, total }))
      .sort((a, b) => a.name.localeCompare(b.name)),
  }
}

// ── buildIngredientList ───────────────────────────────────────────────────────

export function buildIngredientList(entry: ShoppingEntry): Ingredient[] {
  const result: Ingredient[] = []
  if (entry.primary_comp && entry.primary_qty) {
    result.push({ description: entry.primary_comp, quantity: entry.primary_qty * entry.qty })
  }
  for (const sc of entry.secondary_comps) {
    if (sc.description && sc.quantity) {
      result.push({ description: sc.description, quantity: sc.quantity * entry.qty })
    }
  }
  return result
}

