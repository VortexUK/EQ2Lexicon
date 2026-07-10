import { describe, it, expect } from 'vitest'
import {
  diffStats, fmtDelta, diffGear, nullableDelta, sameSubclass, alignTrees, diffTreeNodes,
} from './diff'
import type { CharacterStats, EquipmentSlot } from '../characterSheet'
import type { CharAATree } from '../CharacterAAsTab'
import type { AATreeData } from '../../components/AATree'

// ── Fixtures ─────────────────────────────────────────────────────────────────

const emptyStats = (): CharacterStats => ({
  health_max: null, health_regen: null, power_max: null, power_regen: null,
  run_speed: null, status_points: null,
  str_eff: null, sta_eff: null, agi_eff: null, wis_eff: null, int_eff: null,
  armor: null, avoidance: null, block_chance: null, parry: null,
  mit_physical: null, mit_elemental: null, mit_noxious: null, mit_arcane: null,
  potency: null, crit_chance: null, crit_bonus: null, fervor: null, dps: null,
  double_attack: null, ability_doublecast: null, attack_speed: null,
  strikethrough: null, accuracy: null, ability_mod: null,
  weapon_damage_bonus: null, flurry: null, lethality: null, toughness: null,
  reuse_speed: null, casting_speed: null, recovery_speed: null,
  primary_min: null, primary_max: null, primary_delay: null,
  secondary_min: null, secondary_max: null, secondary_delay: null,
  ranged_min: null, ranged_max: null, ranged_delay: null,
})

const slot = (display: string, itemId: string | null, over: Partial<EquipmentSlot> = {}): EquipmentSlot => ({
  slot: display,
  name: itemId ? `Item ${itemId}` : '',
  item_id: itemId,
  icon_id: null,
  tier: null,
  adorn_slots: [],
  ...over,
})

const tree = (id: number, name: string, spent: Record<string, number>, over: Partial<CharAATree> = {}): CharAATree => ({
  tree_id: id,
  tree_type: 'class',
  tree_name: name,
  spent,
  total_spent: Object.values(spent).reduce((s, v) => s + v, 0),
  ...over,
})

const treeData = (id: number, nodes: [number, string, number, number, number][]): AATreeData => ({
  tree_id: id,
  tree_name: `Tree ${id}`,
  tree_type: 'class',
  nodes: nodes.map(([node_id, name, maxtier, xcoord, ycoord]) => ({
    node_id, name, maxtier, xcoord, ycoord,
    description: '', classification: '', icon_id: 1, backdrop_id: 1,
    pointspertier: 1, points_to_unlock: 0, title: '', spellcrc: 0,
  })),
})

// ── diffStats ────────────────────────────────────────────────────────────────

describe('diffStats', () => {
  it('computes Δ = B − A with sign', () => {
    const a = { ...emptyStats(), str_eff: 100, potency: 50.5 }
    const b = { ...emptyStats(), str_eff: 120, potency: 40.5 }
    const groups = diffStats(a, b)
    const attrs = groups.find(g => g.title === 'Attributes')!
    expect(attrs.rows).toEqual([{ label: 'Strength', fmt: 'int', a: 100, b: 120, delta: 20 }])
    const combat = groups.find(g => g.title === 'Combat')!
    expect(combat.rows[0]).toMatchObject({ label: 'Potency', delta: -10 })
  })

  it('omits both-null rows but keeps one-sided rows with null delta', () => {
    const a = { ...emptyStats(), armor: 5000 }
    const b = emptyStats()
    const groups = diffStats(a, b)
    const defense = groups.find(g => g.title === 'Defense')!
    expect(defense.rows).toEqual([{ label: 'Armor', fmt: 'int', a: 5000, b: null, delta: null }])
    expect(groups.find(g => g.title === 'Attributes')).toBeUndefined() // all rows both-null
  })

  it('derives weapon avg-hit and delay rows', () => {
    const a = { ...emptyStats(), primary_min: 100, primary_max: 300, primary_delay: 2.0 }
    const b = { ...emptyStats(), primary_min: 200, primary_max: 400, primary_delay: 1.5 }
    const weapon = diffStats(a, b).find(g => g.title === 'Weapon')!
    expect(weapon.rows[0]).toMatchObject({ label: 'Primary avg hit', a: 200, b: 300, delta: 100 })
    expect(weapon.rows[1]).toMatchObject({ label: 'Primary delay', delta: -0.5 })
  })

  it('group order matches STAT_GROUPS order', () => {
    const a = { ...emptyStats(), str_eff: 1, armor: 1, potency: 1, reuse_speed: 1 }
    const titles = diffStats(a, a).map(g => g.title)
    expect(titles).toEqual(['Attributes', 'Defense', 'Combat', 'Casting'])
  })
})

describe('fmtDelta', () => {
  it('signs and formats per Fmt', () => {
    expect(fmtDelta(1204, 'int')).toBe(`+${(1204).toLocaleString()}`)
    expect(fmtDelta(-2.34, 'pct1')).toBe('−2.3%')
    expect(fmtDelta(0.5, 'dec1')).toBe('+0.5')
    expect(fmtDelta(0, 'int')).toBe('±0')
  })
})

// ── diffGear ─────────────────────────────────────────────────────────────────

describe('diffGear', () => {
  it('aligns multi-slots positionally (two rings map left/right)', () => {
    const a = [slot('Finger', 'r1'), slot('Finger', 'r2')]
    const b = [slot('Finger', 'r1'), slot('Finger', 'r3')]
    const { right } = diffGear(a, b)
    const leftRing = right.find(r => r.slotKey === 'left_ring')!
    const rightRing = right.find(r => r.slotKey === 'right_ring')!
    expect(leftRing.identical).toBe(true)
    expect(rightRing.identical).toBe(false)
    expect(rightRing.a?.item_id).toBe('r2')
    expect(rightRing.b?.item_id).toBe('r3')
  })

  it('handles a slot present on only one side', () => {
    const a = [slot('Head', 'h1')]
    const b: EquipmentSlot[] = []
    const { left, differingCount } = diffGear(a, b)
    const head = left.find(r => r.slotKey === 'head')!
    expect(head.a?.item_id).toBe('h1')
    expect(head.b).toBeNull()
    expect(head.identical).toBe(false)
    expect(differingCount).toBe(1)
  })

  it('identical requires equal non-null item ids (two empty slots are not "identical" but not differing)', () => {
    const { left, differingCount, occupiedCount } = diffGear([], [])
    expect(left.every(r => !r.identical)).toBe(true)
    expect(differingCount).toBe(0) // both-empty slots don't count as differing
    expect(occupiedCount).toBe(0)
  })

  it('computes adorn fill counts per side', () => {
    const adorned = slot('Head', 'h1', {
      adorn_slots: [
        { color: 'White', adorn_name: 'A', adorn_id: '1', ilvl_bonus: 1 },
        { color: 'Red', adorn_name: null, adorn_id: null, ilvl_bonus: 0 },
      ],
    })
    const { left } = diffGear([adorned], [slot('Head', 'h1')])
    const head = left.find(r => r.slotKey === 'head')!
    expect(head.adornsA).toEqual({ filled: 1, total: 2 })
    expect(head.adornsB).toBeNull() // no adorn slots on B's item
  })

  it('splits consumables out of the differing count', () => {
    const a = [slot('Food', 'f1'), slot('Head', 'h1')]
    const b = [slot('Food', 'f2'), slot('Head', 'h2')]
    const diff = diffGear(a, b)
    expect(diff.consumables.find(r => r.slotKey === 'food')!.identical).toBe(false)
    expect(diff.differingCount).toBe(1) // head only — consumables excluded
  })
})

describe('nullableDelta', () => {
  it('is B − A or null when a side is missing', () => {
    expect(nullableDelta(100, 130)).toBe(30)
    expect(nullableDelta(null, 130)).toBeNull()
  })
})

// ── AAs ──────────────────────────────────────────────────────────────────────

describe('sameSubclass', () => {
  it('matches only equal non-null classes', () => {
    expect(sameSubclass({ cls: 'Templar' }, { cls: 'Templar' })).toBe(true)
    expect(sameSubclass({ cls: 'Templar' }, { cls: 'Inquisitor' })).toBe(false)
    expect(sameSubclass({ cls: null }, { cls: null })).toBe(false)
  })
})

describe('alignTrees', () => {
  it('unions by tree_id, preserving A order and appending B-only trees', () => {
    const a = [tree(1, 'Class', { '10': 5 }), tree(2, 'Heroic', { '20': 3 })]
    const b = [tree(1, 'Class', { '10': 2 }), tree(3, 'Prestige', { '30': 7 })]
    const rows = alignTrees(a, b)
    expect(rows.map(r => r.tree_id)).toEqual([1, 2, 3])
    expect(rows[0]).toMatchObject({ spentA: 5, spentB: 2, delta: -3, onlyOn: null })
    expect(rows[1]).toMatchObject({ spentA: 3, spentB: 0, delta: -3, onlyOn: 'a' })
    expect(rows[2]).toMatchObject({ spentA: 0, spentB: 7, delta: 7, onlyOn: 'b' })
  })
})

describe('diffTreeNodes', () => {
  const meta = treeData(1, [
    // [node_id, name, maxtier, xcoord, ycoord]
    [10, 'Smite', 5, 1, 0],
    [20, 'Heal', 5, 4, 0],
    [30, 'Ward', 8, 1, 2],
  ])

  it('diffs rank differences with Δ = B − A', () => {
    const { rows, differing } = diffTreeNodes({ '10': 5, '20': 3 }, { '10': 2, '20': 3 }, meta)
    const smite = rows.find(r => r.node_id === 10)!
    expect(smite).toMatchObject({ name: 'Smite', rankA: 5, rankB: 2, delta: -3, maxtier: 5 })
    expect(differing).toBe(1)
  })

  it('includes one-sided nodes with rank 0 on the absent side', () => {
    const { rows } = diffTreeNodes({ '30': 8 }, {}, meta)
    expect(rows).toEqual([expect.objectContaining({ node_id: 30, rankA: 8, rankB: 0, delta: -8 })])
  })

  it('omits nodes in neither spend dict', () => {
    const { rows } = diffTreeNodes({ '10': 1 }, { '10': 1 }, meta)
    expect(rows.map(r => r.node_id)).toEqual([10]) // 20 and 30 untouched by either
  })

  it('degrades unknown node ids to a fallback name instead of dropping them', () => {
    const { rows } = diffTreeNodes({ '999': 2 }, {}, meta)
    expect(rows[0]).toMatchObject({ node_id: 999, name: 'Node #999', maxtier: 0 })
  })

  it('sorts in tree reading order (ycoord then xcoord), unknown nodes last', () => {
    const { rows } = diffTreeNodes({ '30': 1, '10': 1, '999': 1, '20': 1 }, {}, meta)
    expect(rows.map(r => r.node_id)).toEqual([10, 20, 30, 999])
  })

  it('handles a null tree (metadata unavailable) without crashing', () => {
    const { rows } = diffTreeNodes({ '10': 3 }, { '10': 1 }, null)
    expect(rows[0]).toMatchObject({ name: 'Node #10', delta: -2 })
  })
})
