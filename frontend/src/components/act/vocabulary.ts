// ── Closed vocabularies for the EQ2Parser-enrichment tag fields ──────────────
//
// Mirrors backend/server/api/act/vocabulary.py (which mirrors EQ2Parser's
// Core Vocabulary). Damage schools are spelled the way the combat log spells
// them; control effects use tooltip vocabulary ("stifle", not the log's
// "silenced"). These never appear in ACT XML exports — they ride the
// /api/act/pack JSON for EQ2Parser users.

export const DAMAGE_SCHOOLS = [
  'crushing',
  'slashing',
  'piercing',
  'heat',
  'cold',
  'magic',
  'mental',
  'divine',
  'disease',
  'poison',
  'focus',
] as const

export const CONTROL_EFFECTS = [
  'stun',
  'stifle',
  'mez',
  'fear',
  'root',
  'snare',
  'daze',
  'charm',
  'knockup',
] as const

/** "poison, disease" → ["poison", "disease"] (order = dominance). */
export function parseSchools(value: string): string[] {
  return value
    .split(',')
    .map(s => s.trim().toLowerCase())
    .filter(s => (DAMAGE_SCHOOLS as readonly string[]).includes(s))
}

/** Toggle one school in the comma-joined list. Newly toggled schools append,
 * so toggle ORDER is dominance order — first picked leads (it drives the
 * parser's flame colour). */
export function toggleSchool(value: string, school: string): string {
  const schools = parseSchools(value)
  const next = schools.includes(school)
    ? schools.filter(s => s !== school)
    : [...schools, school]
  return next.join(', ')
}
