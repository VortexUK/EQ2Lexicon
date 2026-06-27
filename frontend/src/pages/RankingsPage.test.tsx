/**
 * buildClassOptions — the grouped class-filter dropdown for the rankings page.
 * Pure-function tests (no rendering): "All classes" first, then per-archetype an
 * "All <archetype>s" selectable row followed by its classes, in canonical
 * archetype order.
 */
import { describe, it, expect } from 'vitest'

import { buildClassOptions } from './RankingsPage'

const ARCHETYPE = new Map<string, string>([
  ['Berserker', 'Fighter'],
  ['Guardian', 'Fighter'],
  ['Templar', 'Priest'],
  ['Wizard', 'Mage'],
  ['Assassin', 'Scout'],
])
const archetypeOf = (c: string) => ARCHETYPE.get(c)

describe('buildClassOptions', () => {
  it('returns only "All classes" when no classes are present', () => {
    expect(buildClassOptions([], archetypeOf)).toEqual([{ value: '', label: 'All classes' }])
  })

  it('groups classes under selectable archetypes in canonical order', () => {
    const opts = buildClassOptions(['Wizard', 'Templar', 'Guardian', 'Berserker'], archetypeOf)

    // "All classes" first, ungrouped.
    expect(opts[0]).toEqual({ value: '', label: 'All classes' })

    // Archetype order is Fighter → Priest → Scout → Mage (Scout absent here).
    expect(opts.slice(1)).toEqual([
      { value: 'Fighter', label: 'All Fighters', group: 'Fighters' },
      { value: 'Berserker', label: 'Berserker', group: 'Fighters' },
      { value: 'Guardian', label: 'Guardian', group: 'Fighters' },
      { value: 'Priest', label: 'All Priests', group: 'Priests' },
      { value: 'Templar', label: 'Templar', group: 'Priests' },
      { value: 'Mage', label: 'All Mages', group: 'Mages' },
      { value: 'Wizard', label: 'Wizard', group: 'Mages' },
    ])
  })

  it('buckets classes with no known archetype under "Other" (last)', () => {
    const opts = buildClassOptions(['Wizard', 'Mystery'], archetypeOf)
    const values = opts.map(o => o.value)
    // Mage group precedes the catch-all Other group.
    expect(values).toEqual(['', 'Mage', 'Wizard', 'Other', 'Mystery'])
    expect(opts.find(o => o.value === 'Mystery')?.group).toBe('Others')
  })
})
