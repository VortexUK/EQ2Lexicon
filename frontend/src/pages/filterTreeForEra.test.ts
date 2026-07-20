/**
 * filterTreeForEra — the era node filter behind the AA tab (and the
 * upcoming planner): hide tree rows that don't exist in the server's
 * expansion (e.g. the class tree's Sentinel's-Fate rows on an EoF server).
 */
import { describe, expect, it } from 'vitest'

import type { AATreeData } from '../components/AATree'
import { type AAConfig, filterTreeForEra } from './CharacterAAsTab'

const node = (id: number, ycoord: number) => ({
  node_id: id,
  name: `Node ${id}`,
  description: '',
  classification: 'Strength',
  xcoord: 1,
  ycoord,
  icon_id: 0,
  backdrop_id: -1,
  maxtier: 10,
  pointspertier: 1,
  points_to_unlock: 0,
  title: '',
  spellcrc: 0,
})

const TREE: AATreeData = {
  tree_id: 42,
  tree_name: 'Shaman',
  tree_type: 'class',
  nodes: [node(1, 0), node(2, 1), node(3, 4), node(4, 5), node(5, 6)],
}

const config = (visible?: Record<string, number[]>): AAConfig => ({
  xpac: 'Echoes of Faydwer',
  aa_cap: 100,
  tradeskill_aa_cap: 45,
  unlocked_tree_types: ['class', 'subclass', 'tradeskill'],
  visible_rows: visible,
})

describe('filterTreeForEra', () => {
  it('drops nodes on rows the era does not have', () => {
    const filtered = filterTreeForEra(TREE, 'class', config({ class: [0, 1, 2, 3, 4] }))
    expect(filtered.nodes.map(n => n.node_id)).toEqual([1, 2, 3]) // rows 5+6 gone
  })

  it('leaves trees alone when their type has no visible_rows entry', () => {
    const filtered = filterTreeForEra(TREE, 'class', config({ subclass: [0, 3] }))
    expect(filtered.nodes).toHaveLength(5)
  })

  it('leaves everything alone when visible_rows is absent (SF+ eras / old config)', () => {
    expect(filterTreeForEra(TREE, 'class', config(undefined)).nodes).toHaveLength(5)
  })

  it('does not mutate the cached tree object', () => {
    filterTreeForEra(TREE, 'class', config({ class: [0] }))
    expect(TREE.nodes).toHaveLength(5)
  })
})
