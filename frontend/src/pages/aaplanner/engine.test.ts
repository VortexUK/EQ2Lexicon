/**
 * aaPlanner engine tests — the rule semantics the planner hard-blocks on:
 * point-cost-weighted thresholds, self-exclusive unlock counters, parent
 * rank prereqs, the flat 100/tree cap, separate pools, and no-stranding
 * removals.
 */
import { describe, expect, it } from 'vitest'

import type { AANode, AATreeData } from '../../components/AATree'
import {
  type PlanAllocations,
  type PlannerCtx,
  addRank,
  canAddRank,
  canRemoveRank,
  linePoints,
  planFromSpent,
  poolPoints,
  removeRank,
  treePoints,
  validatePlan,
} from './engine'

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

// A miniature Shaman-style class tree: three 10-rank Strength skills, the
// 22-point line-final (the user's Spiritual Foresight example), and a
// 2-point/rank endline gated on 16 points in tree.
const CLASS_TREE: AATreeData = {
  tree_id: 42,
  tree_name: 'Shaman',
  tree_type: 'class',
  nodes: [
    node(1, { name: 'Leg Bite' }),
    node(2, { name: 'Aura of Haste' }),
    node(3, { name: 'Aura of Warding' }),
    node(4, { name: 'Spiritual Foresight', maxtier: 1, classification_points_required: 22 }),
    node(5, { name: 'Endline', maxtier: 2, pointspertier: 2, points_to_unlock: 16, classification: '' }),
    node(6, { name: 'Chained', maxtier: 5, first_parent_id: 1, first_parent_required_tier: 5 }),
  ],
}

const TS_TREE: AATreeData = {
  tree_id: 77,
  tree_name: 'Craftsman',
  tree_type: 'tradeskill',
  nodes: [node(70, { classification: 'Crafting Expertise', maxtier: 45 })],
}

const ctx: PlannerCtx = { trees: [CLASS_TREE, TS_TREE], aaCap: 100, tradeskillCap: 45 }

const alloc = (a: Record<string, number>): PlanAllocations => ({ '42': a })

describe('point counting', () => {
  it('weights by pointspertier, not rank count', () => {
    const plan = alloc({ '1': 3, '5': 2 }) // 3×1 + 2×2
    expect(treePoints(CLASS_TREE, plan['42'])).toBe(7)
  })

  it('linePoints only counts the classification and honours exclusion', () => {
    const plan = alloc({ '1': 4, '5': 2 }) // Endline has no classification
    expect(linePoints(CLASS_TREE, plan['42'], 'Strength')).toBe(4)
    expect(linePoints(CLASS_TREE, plan['42'], 'Strength', 1)).toBe(0)
  })

  it('pools are separate: tradeskill spend never counts as adventure', () => {
    const plan: PlanAllocations = { '42': { '1': 2 }, '77': { '70': 10 } }
    expect(poolPoints(ctx, plan, 'adventure')).toBe(2)
    expect(poolPoints(ctx, plan, 'tradeskill')).toBe(10)
  })
})

describe('canAddRank', () => {
  it('blocks the 22-point line-final until the line total reaches 22', () => {
    const spiritualForesight = CLASS_TREE.nodes[3]
    const at21 = alloc({ '1': 10, '2': 10, '3': 1 })
    expect(canAddRank(ctx, at21, CLASS_TREE, spiritualForesight).ok).toBe(false)
    const at22 = alloc({ '1': 10, '2': 10, '3': 2 })
    expect(canAddRank(ctx, at22, CLASS_TREE, spiritualForesight).ok).toBe(true)
  })

  it('tree-points gates count 2-point ranks at their cost', () => {
    const endline = CLASS_TREE.nodes[4] // needs 16 in tree
    const plan = alloc({ '1': 8, '2': 8 })
    expect(canAddRank(ctx, plan, CLASS_TREE, endline).ok).toBe(true)
    expect(canAddRank(ctx, alloc({ '1': 8, '2': 7 }), CLASS_TREE, endline).ok).toBe(false)
  })

  it('a node cannot satisfy its own threshold (self-exclusive)', () => {
    // 16 needed in tree; endline rank 1 (2 pts) + 14 elsewhere = 16 total,
    // but only 14 excluding itself → second rank stays locked.
    const endline = CLASS_TREE.nodes[4]
    const plan = alloc({ '1': 10, '2': 4, '5': 1 })
    expect(canAddRank(ctx, plan, CLASS_TREE, endline).ok).toBe(false)
  })

  it('enforces parent rank prereqs and maxtier', () => {
    const chained = CLASS_TREE.nodes[5]
    expect(canAddRank(ctx, alloc({ '1': 4 }), CLASS_TREE, chained).ok).toBe(false)
    expect(canAddRank(ctx, alloc({ '1': 5 }), CLASS_TREE, chained).ok).toBe(true)
    const maxed = alloc({ '1': 10 })
    expect(canAddRank(ctx, maxed, CLASS_TREE, CLASS_TREE.nodes[0])).toEqual({
      ok: false,
      reason: 'Already at max rank',
    })
  })

  it('enforces the adventure cap and the flat 100 tree cap', () => {
    const tight: PlannerCtx = { ...ctx, aaCap: 12 }
    const plan = alloc({ '1': 10, '2': 2 })
    expect(canAddRank(tight, plan, CLASS_TREE, CLASS_TREE.nodes[2]).ok).toBe(false)

    // Tree cap: a wide synthetic tree that can exceed 100 under a big aaCap.
    const wide: AATreeData = {
      tree_id: 9,
      tree_name: 'Wide',
      tree_type: 'subclass',
      nodes: Array.from({ length: 11 }, (_, i) => node(900 + i, { classification: '', maxtier: 10 })),
    }
    const wideCtx: PlannerCtx = { trees: [wide], aaCap: 200, tradeskillCap: 0 }
    const full: PlanAllocations = { '9': Object.fromEntries(Array.from({ length: 10 }, (_, i) => [String(900 + i), 10])) }
    expect(canAddRank(wideCtx, full, wide, wide.nodes[10])).toEqual({
      ok: false,
      reason: 'Tree cap reached (100 points)',
    })
  })

  it('tradeskill spend draws from its own cap, not the adventure cap', () => {
    const tiny: PlannerCtx = { ...ctx, aaCap: 1, tradeskillCap: 45 }
    const plan: PlanAllocations = { '42': { '1': 1 } } // adventure cap exhausted
    expect(canAddRank(tiny, plan, TS_TREE, TS_TREE.nodes[0]).ok).toBe(true)
  })
})

describe('shadows section gating (structural rule)', () => {
  // Two sections: General (y=1) and Priest (y=6) — the TSO shadows shape.
  const SHADOWS: AATreeData = {
    tree_id: 49,
    tree_name: 'Shadows',
    tree_type: 'shadows',
    nodes: [
      node(201, { name: 'General One', classification: 'General', ycoord: 1, maxtier: 5 }),
      node(202, { name: 'General Two', classification: 'General', ycoord: 1, maxtier: 5 }),
      node(211, { name: 'Litany of Combat', classification: 'Priest', ycoord: 6, maxtier: 5 }),
    ],
  }
  const sctx: PlannerCtx = { trees: [SHADOWS], aaCap: 200, tradeskillCap: 0 }
  const litany = SHADOWS.nodes[2]

  it('blocks the second section until 5 points sit in the one above', () => {
    const at4: PlanAllocations = { '49': { '201': 2, '202': 2 } }
    const verdict = canAddRank(sctx, at4, SHADOWS, litany)
    expect(verdict).toEqual({ ok: false, reason: 'Requires 5 points spent in General' })
    const at5: PlanAllocations = { '49': { '201': 3, '202': 2 } }
    expect(canAddRank(sctx, at5, SHADOWS, litany).ok).toBe(true)
  })

  it('first-section nodes are never section-gated', () => {
    expect(canAddRank(sctx, {}, SHADOWS, SHADOWS.nodes[0]).ok).toBe(true)
  })

  it('blocks refunds that would drop the section below a taken dependent', () => {
    const plan: PlanAllocations = { '49': { '201': 3, '202': 2, '211': 1 } }
    const verdict = canRemoveRank(sctx, plan, SHADOWS, SHADOWS.nodes[0])
    expect(verdict.ok).toBe(false)
    expect(verdict.reason).toContain('Litany of Combat')
  })
})

describe('canRemoveRank — no stranding', () => {
  it('blocks dropping the line below a taken 22-point final', () => {
    const plan = alloc({ '1': 10, '2': 10, '3': 2, '4': 1 })
    const verdict = canRemoveRank(ctx, plan, CLASS_TREE, CLASS_TREE.nodes[2])
    expect(verdict.ok).toBe(false)
    expect(verdict.reason).toContain('Spiritual Foresight')
  })

  it('allows the same removal when there is slack in the line', () => {
    const plan = alloc({ '1': 10, '2': 10, '3': 3, '4': 1 }) // 23 in line
    expect(canRemoveRank(ctx, plan, CLASS_TREE, CLASS_TREE.nodes[2]).ok).toBe(true)
  })

  it('blocks dropping a parent below a chained child requirement', () => {
    const plan = alloc({ '1': 5, '6': 1 })
    expect(canRemoveRank(ctx, plan, CLASS_TREE, CLASS_TREE.nodes[0]).ok).toBe(false)
  })

  it('refuses removals from empty nodes', () => {
    expect(canRemoveRank(ctx, alloc({}), CLASS_TREE, CLASS_TREE.nodes[0]).ok).toBe(false)
  })
})

describe('plan mutation + seeding', () => {
  it('addRank/removeRank are immutable and drop zero ranks', () => {
    const plan = alloc({ '1': 1 })
    const added = addRank(plan, CLASS_TREE, CLASS_TREE.nodes[0])
    expect(added['42']['1']).toBe(2)
    expect(plan['42']['1']).toBe(1)
    const removed = removeRank(removeRank(added, CLASS_TREE, CLASS_TREE.nodes[0]), CLASS_TREE, CLASS_TREE.nodes[0])
    expect('1' in removed['42']).toBe(false)
  })

  it('planFromSpent copies the character spent maps', () => {
    const plan = planFromSpent([{ tree_id: 42, spent: { '1': 7 } }])
    expect(plan['42']).toEqual({ '1': 7 })
  })

  it('validatePlan flags an imported over-cap or stranded build', () => {
    const stranded = alloc({ '1': 10, '2': 10, '4': 1 }) // line at 20 < 22 with the final taken
    const violations = validatePlan(ctx, stranded)
    expect(violations.some(v => v.nodeName === 'Spiritual Foresight')).toBe(true)
  })
})
