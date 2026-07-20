/**
 * aaFile exporter tests — legal purchase ordering (the in-game loader
 * replays the file top-to-bottom) and the .aa XML shape reverse-engineered
 * from a real in-game export.
 */
import { describe, expect, it } from 'vitest'

import type { AANode, AATreeData } from '../../components/AATree'
import { aaFileName, buildAAFileXml } from './aaFile'
import { type PlanAllocations, type PlannerCtx, spendOrder } from './engine'

const node = (id: number, over: Partial<AANode> = {}): AANode => ({
  node_id: id,
  name: `Node ${id}`,
  description: '',
  classification: 'Strength',
  xcoord: 1,
  ycoord: 1,
  icon_id: 0,
  backdrop_id: -1,
  maxtier: 10,
  pointspertier: 1,
  points_to_unlock: 0,
  title: '',
  spellcrc: 0,
  ...over,
})

const CLASS_TREE: AATreeData = {
  tree_id: 3,
  tree_name: 'Cleric',
  tree_type: 'class',
  nodes: [
    node(101, { ycoord: 1 }),
    node(102, { ycoord: 2 }),
    node(103, { ycoord: 3 }),
    node(104, { name: 'Line Final', ycoord: 4, maxtier: 1, classification_points_required: 22 }),
  ],
}

const TS_TREE: AATreeData = {
  tree_id: 73,
  tree_name: 'Tradeskill',
  tree_type: 'tradeskill',
  nodes: [node(700, { classification: 'Crafting Expertise', maxtier: 45 })],
}

const ctx: PlannerCtx = { trees: [CLASS_TREE, TS_TREE], aaCap: 200, tradeskillCap: 45 }

describe('spendOrder', () => {
  it('places gated ranks after their requirements are met', () => {
    const plan: PlanAllocations = { '3': { '101': 10, '102': 10, '103': 2, '104': 1 } }
    const { steps, unplaced } = spendOrder(ctx, plan)
    expect(unplaced).toHaveLength(0)
    expect(steps).toHaveLength(23)
    // The 22-point line final must be the last purchase.
    expect(steps[22]).toEqual({ tree_id: 3, node_id: 104 })
    // A node's ranks run consecutively (game export shape).
    expect(steps.slice(0, 10).every(s => s.node_id === 101)).toBe(true)
  })

  it('returns unreachable ranks as unplaced instead of emitting them', () => {
    const stranded: PlanAllocations = { '3': { '101': 10, '102': 10, '104': 1 } } // line at 20 < 22
    const { steps, unplaced } = spendOrder(ctx, stranded)
    expect(steps).toHaveLength(20)
    expect(unplaced).toEqual([{ tree_id: 3, node_id: 104 }])
  })
})

describe('buildAAFileXml', () => {
  it('buckets by typenum with per-bucket order counters and repeats ids per rank', () => {
    const plan: PlanAllocations = { '3': { '101': 3 }, '73': { '700': 2 } }
    const { xml, unplacedCount } = buildAAFileXml(ctx, plan)
    expect(unplacedCount).toBe(0)

    const doc = new DOMParser().parseFromString(xml, 'application/xml')
    expect(doc.documentElement.tagName).toBe('aa')
    expect(doc.documentElement.getAttribute('game')).toBe('eq2')

    const sections = [...doc.querySelectorAll('alternateadvancements')]
    const byType = Object.fromEntries(sections.map(s => [s.getAttribute('typenum'), s]))
    // Adventure bucket: 3 entries for node 101, orders 1..3, treeID 3.
    const adv = [...byType['0'].querySelectorAll('alternateadvancement')]
    expect(adv.map(e => [e.getAttribute('id'), e.getAttribute('order'), e.getAttribute('treeID')])).toEqual([
      ['101', '1', '3'],
      ['101', '2', '3'],
      ['101', '3', '3'],
    ])
    // Tradeskill bucket restarts its order counter at 1.
    const ts = [...byType['3'].querySelectorAll('alternateadvancement')]
    expect(ts.map(e => e.getAttribute('order'))).toEqual(['1', '2'])
    // The game always writes an (empty) bucket 2 — mirrored for shape parity.
    expect(byType['2']).toBeTruthy()
    expect(byType['2'].children).toHaveLength(0)
  })

  it('reports skipped ranks so the UI can warn', () => {
    const stranded: PlanAllocations = { '3': { '104': 1 } }
    const { xml, unplacedCount } = buildAAFileXml(ctx, stranded)
    expect(unplacedCount).toBe(1)
    expect(xml).not.toContain('id="104"')
  })
})

describe('aaFileName', () => {
  it('sanitises to a safe filename', () => {
    expect(aaFileName('Menludiir', 'Raid DPS!')).toBe('Menludiir_Raid_DPS.aa')
    expect(aaFileName('X', '   ')).toBe('X.aa')
  })
})
