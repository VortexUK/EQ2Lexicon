import { describe, it, expect } from 'vitest'

import { partitionAASpend } from './aaSpend'
import type { CharAATree } from './CharacterAAsTab'

function tree(tree_type: string, total_spent: number): CharAATree {
  return { tree_id: 0, tree_type, tree_name: tree_type, spent: {}, total_spent }
}

describe('partitionAASpend', () => {
  it('sums adventure and tradeskill pools separately', () => {
    const trees = [
      tree('class', 100),
      tree('subclass', 40),
      tree('shadows', 30),
      tree('tradeskill', 20),
      tree('tradeskill_general', 15),
    ]
    expect(partitionAASpend(trees)).toEqual({ adventure: 170, tradeskill: 35 })
  })

  it('keeps tradeskill out of the adventure total', () => {
    // A build with only tradeskill AAs contributes nothing to adventure.
    expect(partitionAASpend([tree('tradeskill', 45)])).toEqual({ adventure: 0, tradeskill: 45 })
  })

  it('is zero for an empty tree list', () => {
    expect(partitionAASpend([])).toEqual({ adventure: 0, tradeskill: 0 })
  })
})
