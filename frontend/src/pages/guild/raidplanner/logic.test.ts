import { describe, expect, it } from 'vitest'

import type { ClassInfo } from '../../../useClasses'
import { altOwnerLabel, buildGrid, computeWarnings, moveCharacter, nextRaidDate, placementsEqual, swapGroups } from './logic'
import type { Placement } from './types'

const P = (name: string, group: number | null, slot: number | null, sitout = false): Placement => ({
  character_name: name,
  group_num: group,
  slot,
  sitout,
})

const cls = (name: string, archetype: string, subclass: string | null): ClassInfo => ({
  name,
  archetype,
  subclass,
  role: 'x',
  colour: '#fff',
  display_order: 0,
  icon_url: '',
})

const CLASSMAP = new Map<string, ClassInfo | undefined>([
  ['tanky', cls('Guardian', 'Fighter', 'Warrior')],
  ['healy', cls('Templar', 'Priest', 'Cleric')],
  ['bardy', cls('Troubador', 'Scout', 'Bard')],
  ['chanty', cls('Illusionist', 'Mage', 'Enchanter')],
  ['nukey', cls('Wizard', 'Mage', 'Sorcerer')],
])

describe('moveCharacter', () => {
  it('places a benched character into an empty slot', () => {
    const next = moveCharacter([], 'Tanky', { kind: 'slot', group: 1, slot: 0 })
    expect(next).toEqual([P('Tanky', 1, 0)])
  })

  it('swaps when dropping onto an occupied slot', () => {
    const start = [P('Tanky', 1, 0), P('Healy', 2, 3)]
    const next = moveCharacter(start, 'Tanky', { kind: 'slot', group: 2, slot: 3 })
    const byName = Object.fromEntries(next.map(p => [p.character_name, p]))
    expect(byName['Tanky']).toMatchObject({ group_num: 2, slot: 3 })
    expect(byName['Healy']).toMatchObject({ group_num: 1, slot: 0 })
  })

  it('bench→occupied slot sends the occupant to the bench', () => {
    const start = [P('Healy', 1, 0)]
    const next = moveCharacter(start, 'Tanky', { kind: 'slot', group: 1, slot: 0 })
    expect(next).toEqual([P('Tanky', 1, 0)]) // Healy has no row = benched
  })

  it('sitout→occupied slot sends the occupant to sitout', () => {
    const start = [P('Tanky', null, null, true), P('Healy', 1, 0)]
    const next = moveCharacter(start, 'Tanky', { kind: 'slot', group: 1, slot: 0 })
    const byName = Object.fromEntries(next.map(p => [p.character_name, p]))
    expect(byName['Tanky']).toMatchObject({ group_num: 1, slot: 0, sitout: false })
    expect(byName['Healy']).toMatchObject({ sitout: true })
  })

  it('moves to sitout and bench', () => {
    let next = moveCharacter([P('Tanky', 1, 0)], 'Tanky', { kind: 'sitout' })
    expect(next).toEqual([P('Tanky', null, null, true)])
    next = moveCharacter(next, 'Tanky', { kind: 'bench' })
    expect(next).toEqual([])
  })

  it('is case-insensitive on names', () => {
    const next = moveCharacter([P('Tanky', 1, 0)], 'tAnKy', { kind: 'sitout' })
    expect(next).toEqual([P('tAnKy', null, null, true)])
  })
})

describe('swapGroups', () => {
  it('swaps every member of two groups and leaves others alone', () => {
    const start = [P('Tanky', 1, 0), P('Healy', 1, 1), P('Nukey', 2, 0), P('Bardy', 3, 5)]
    const next = swapGroups(start, 1, 2)
    const byName = Object.fromEntries(next.map(p => [p.character_name, p.group_num]))
    expect(byName).toEqual({ Tanky: 2, Healy: 2, Nukey: 1, Bardy: 3 })
  })
})

describe('buildGrid', () => {
  it('splits placements into groups, sitout and bench', () => {
    const placements = [P('Tanky', 1, 0), P('Healy', null, null, true)]
    const grid = buildGrid(placements, ['Tanky', 'Healy', 'Bardy'])
    expect(grid.groups[0][0]?.character_name).toBe('Tanky')
    expect(grid.sitout.map(p => p.character_name)).toEqual(['Healy'])
    expect(grid.bench).toEqual(['Bardy'])
  })
})

describe('computeWarnings', () => {
  const data = { availability: {} as Record<string, 'tentative' | 'afk'>, players: {} as Record<string, string> }

  it('flags missing priest, bard and enchanter per non-empty group', () => {
    const w = computeWarnings(data, [P('Nukey', 1, 0)], CLASSMAP)
    const texts = w.map(x => x.text)
    expect(texts).toContain('Group 1 has no priest')
    expect(texts).toContain('Group 1 has no bard')
    expect(texts).toContain('Group 1 has no enchanter')
  })

  it('is silent for a group with priest + bard + enchanter and for empty groups', () => {
    const w = computeWarnings(data, [P('Healy', 1, 0), P('Bardy', 1, 1), P('Chanty', 1, 2)], CLASSMAP)
    expect(w).toEqual([])
  })

  it('flags AFK characters placed in groups but not on sitout', () => {
    const d = { ...data, availability: { tanky: 'afk' as const, healy: 'afk' as const } }
    const w = computeWarnings(d, [P('Tanky', 1, 0), P('Healy', null, null, true), P('Bardy', 1, 1), P('Chanty', 1, 2)], CLASSMAP)
    const texts = w.map(x => x.text)
    expect(texts).toContain('Tanky is AFK on this date')
    expect(texts.some(t => t.includes('Healy is AFK'))).toBe(false)
  })

  it('flags the same player fielding two characters in groups', () => {
    const d = { ...data, players: { tanky: 'Ben', nukey: 'Ben', healy: 'Sue' } }
    const w = computeWarnings(
      d,
      [P('Tanky', 1, 0), P('Nukey', 2, 0), P('Healy', 1, 1), P('Bardy', 1, 2), P('Chanty', 1, 3), P('Bardy2', 2, 1)],
      CLASSMAP,
    )
    const dupe = w.find(x => x.text.includes('Ben has 2 characters'))
    expect(dupe).toBeTruthy()
    expect(dupe!.severity).toBe('info')
  })
})

describe('nextRaidDate', () => {
  it('returns today when today is a raid day', () => {
    const wed = new Date('2026-07-15T12:00:00Z') // a Wednesday
    expect(nextRaidDate([[3]], wed)).toBe('2026-07-15')
  })

  it('finds the next matching weekday', () => {
    const wed = new Date('2026-07-15T12:00:00Z')
    expect(nextRaidDate([[5]], wed)).toBe('2026-07-17') // Friday
    expect(nextRaidDate([[1]], wed)).toBe('2026-07-20') // Monday
  })

  it('handles ISO Sunday (7) and falls back to today with no raids', () => {
    const sat = new Date('2026-07-18T12:00:00Z')
    expect(nextRaidDate([[7]], sat)).toBe('2026-07-19')
    expect(nextRaidDate([], sat)).toBe('2026-07-18')
  })
})

describe('placementsEqual', () => {
  it('ignores order and matches on content', () => {
    const a = [P('Tanky', 1, 0), P('Healy', null, null, true)]
    const b = [P('Healy', null, null, true), P('Tanky', 1, 0)]
    expect(placementsEqual(a, b)).toBe(true)
    expect(placementsEqual(a, [P('Tanky', 1, 1), P('Healy', null, null, true)])).toBe(false)
  })
})

describe('altOwnerLabel', () => {
  const roster = [
    { name: 'Menludiir', role: 'raider' as const },
    { name: 'Menwardiir', role: 'raid_alt' as const },
    { name: 'Menthird', role: 'raider' as const },
    { name: 'Stranger', role: 'raider' as const },
  ]
  const players = { menludiir: 'Ben', menwardiir: 'Ben', menthird: 'Ben', stranger: 'Sue' }

  it("names the owner's placed raider first", () => {
    const owner = altOwnerLabel('Menwardiir', players, roster, [P('Menthird', 1, 0)])
    expect(owner).toBe('Menthird')
  })

  it("falls back to the owner's unplaced raider", () => {
    const owner = altOwnerLabel('Menwardiir', players, roster, [])
    expect(owner).toBe('Menludiir')
  })

  it('falls back to the player display name when they have no other character', () => {
    const owner = altOwnerLabel('Menwardiir', { menwardiir: 'Ben' }, [{ name: 'Menwardiir', role: 'raid_alt' }], [])
    expect(owner).toBe('Ben')
  })

  it('returns null for an unclaimed alt', () => {
    expect(altOwnerLabel('Menwardiir', {}, roster, [])).toBeNull()
  })
})
