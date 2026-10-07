/**
 * Canonical EQ2 rarity / tier colours — the single source of truth for the
 * frontend. Item quality names, recipe tiers, and the in-game-style tooltips
 * all resolve through here. The actual hex values live as `--rarity-*` CSS
 * tokens in index.css; this module just maps the various name spellings onto
 * them. Never define a per-page colour map.
 */

const COMMON = 'var(--rarity-common)'
const HANDCRAFTED = 'var(--rarity-handcrafted)'
const TREASURED = 'var(--rarity-treasured)'
const LEGENDARY = 'var(--rarity-legendary)'
const FABLED = 'var(--rarity-fabled)'
const MYTHICAL = 'var(--rarity-mythical)'
const ETHEREAL = 'var(--rarity-ethereal)'
const CELESTIAL = 'var(--rarity-celestial)'
const ANCIENT = 'var(--rarity-ancient)'

/** Single item-quality words → colour. Lowercased keys. */
const QUALITY_WORD: Record<string, string> = {
  common: COMMON,
  handcrafted: HANDCRAFTED,
  uncommon: HANDCRAFTED,
  treasured: TREASURED,
  mastercrafted: TREASURED,
  legendary: LEGENDARY,
  fabled: FABLED,
  mythical: MYTHICAL,
  ethereal: ETHEREAL,
  celestial: CELESTIAL,
  ancient: ANCIENT,
}

/**
 * Colour for an item-quality string. Case-insensitive; handles compound
 * qualities like "Mastercrafted Fabled" by taking the last recognised word
 * (so it reads as Fabled, not Mastercrafted). Returns `fallback` if nothing
 * matches.
 */
export function itemRarityColor(quality: string | null | undefined, fallback = 'var(--text)'): string {
  if (!quality) return fallback
  const key = quality.trim().toLowerCase()
  if (QUALITY_WORD[key]) return QUALITY_WORD[key]
  const words = key.split(/\s+/)
  for (let i = words.length - 1; i >= 0; i--) {
    if (QUALITY_WORD[words[i]]) return QUALITY_WORD[words[i]]
  }
  return fallback
}

/** Crafting recipe tier names map onto the same rarity ladder. */
const RECIPE_TIER: Record<string, string> = {
  apprentice: COMMON,
  journeyman: HANDCRAFTED,
  adept: TREASURED,
  expert: LEGENDARY,
  master: FABLED,
  grandmaster: CELESTIAL,
  ancient: ANCIENT,
}

/** Colour for a crafting recipe tier (Apprentice … Ancient). */
export function recipeTierColor(tier: string | null | undefined, fallback = 'var(--text-muted)'): string {
  if (!tier) return fallback
  return RECIPE_TIER[tier.trim().toLowerCase()] ?? fallback
}

export interface QualityStyle {
  color: string
  /** Darker glow for the tooltip text-shadow — mimics the EQ2 client. */
  glowColor?: string
}

/** Per-rarity glow used only by the in-game-style tooltips. */
const GLOW_WORD: Record<string, string> = {
  fabled: '#df535f',
  legendary: '#d56900',
  treasured: '#d56900',
  mastercrafted: '#d56900',
}

/**
 * Tooltip quality style: the canonical rarity colour plus the darker glow the
 * in-game-style tooltips use for their text-shadow. Falls back to a neutral
 * off-white when the quality isn't recognised.
 */
export function qualityStyle(q: string | null | undefined): QualityStyle {
  const color = itemRarityColor(q, '#dcdcdc')
  if (!q) return { color }
  const key = q.trim().toLowerCase()
  if (GLOW_WORD[key]) return { color, glowColor: GLOW_WORD[key] }
  const words = key.split(/\s+/)
  for (let i = words.length - 1; i >= 0; i--) {
    if (GLOW_WORD[words[i]]) return { color, glowColor: GLOW_WORD[words[i]] }
  }
  return { color }
}
