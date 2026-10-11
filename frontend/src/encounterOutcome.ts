// One source of truth for how an encounter's `success_level` is shown: the
// parse page title, the /parses fight rows and the admin parses table all read
// it from here. Do not re-declare the mapping in a page.
//
// The number is ACT's GetEncounterSuccessLevel(), passed through by the ACT
// plugin and reimplemented by EQ2Parser (backend: IngestEncounter.success):
//   0 = indeterminate
//   1 = win: the main enemy died AND at least one ally player survived
//   2 = partial / unclear: exactly one of those two is true (the enemy died but
//       every player died at some point, or the enemy lived and at least one
//       player never died)
//   3 = loss: the enemy lived and no player survived (a wipe)
// Display only. Every rule on the site (rankings, retention, kill progress)
// tests `=== 1`.

export type EncounterOutcome = 'win' | 'partial' | 'loss' | 'unknown'

export function encounterOutcome(successLevel: number): EncounterOutcome {
  switch (successLevel) {
    case 1:  return 'win'
    case 2:  return 'partial'
    case 3:  return 'loss'
    default: return 'unknown'
  }
}

// Semantic design tokens (index.css @theme), never a hex.
const OUTCOME_COLOR: Record<Exclude<EncounterOutcome, 'unknown'>, string> = {
  win:     'var(--color-success)',
  partial: 'var(--color-warning)',
  loss:    'var(--color-danger)',
}

/**
 * Text colour for an encounter title, for a data-driven `style={{ color }}`.
 * `unknownColor` is what the caller shows for an indeterminate outcome (the
 * parse page keeps its gold heading, the list keeps plain text).
 */
export function outcomeColor(successLevel: number, unknownColor: string): string {
  const outcome = encounterOutcome(successLevel)
  return outcome === 'unknown' ? unknownColor : OUTCOME_COLOR[outcome]
}

/** Short lowercase label for tables: win / partial / loss, a dash when unknown. */
export function outcomeLabel(successLevel: number): string {
  const outcome = encounterOutcome(successLevel)
  return outcome === 'unknown' ? '—' : outcome
}
