/**
 * aaPlanner — pure validation + allocation engine for the AA planner.
 *
 * Rule semantics (verified against aas.db node data + in-game behaviour):
 *   - Every threshold counts AA POINTS SPENT (rank × pointspertier), never
 *     rank counts — a 2-point/rank endline contributes 2 per rank.
 *   - Unlock thresholds are SELF-EXCLUSIVE: a node's own points never count
 *     toward its own requirement (the game checks the unlock before rank 1
 *     goes in). This also makes "is this allocation achievable in some spend
 *     order" order-independent, because real trees gate rows monotonically.
 *   - Removals must not strand anything: dropping a rank is legal only if
 *     every other taken node still meets its requirements afterwards
 *     (validatePlan on the simulated allocation).
 *   - Flat TREE_POINT_CAP (100) per tree in every era; adventure vs
 *     tradeskill trees draw from separate pools with separate caps.
 *
 * Kept free of React so it unit-tests directly (engine.test.ts).
 */

import type { AANode, AATreeData } from '../../components/AATree'

export const TREE_POINT_CAP = 100
export const TRADESKILL_TREE_TYPES: ReadonlySet<string> = new Set(['tradeskill', 'tradeskill_general'])

/** Shadows trees gate each section (General → Archetype → Class → Subclass)
 * on points spent in the section above. This rule is STRUCTURAL — the Census
 * node data carries no field for it (only each section's own endline has a
 * classification_points_required), so the threshold lives here. */
export const SHADOWS_SECTION_UNLOCK = 5

/** node_id (string, matches the API's spent maps) → rank taken. */
export type Allocation = Record<string, number>
/** tree_id (string) → that tree's allocation. */
export type PlanAllocations = Record<string, Allocation>

export interface PlannerCtx {
  trees: AATreeData[] // era-filtered (filterTreeForEra) — the plannable trees
  aaCap: number // adventure AA cap for the xpac
  tradeskillCap: number // separate tradeskill pool cap
}

export interface Verdict {
  ok: boolean
  reason?: string
}

export interface PlanViolation {
  treeId: number
  nodeId: number
  nodeName: string
  reason: string
}

const rankOf = (alloc: Allocation | undefined, nodeId: number): number => alloc?.[String(nodeId)] ?? 0

const isTradeskill = (tree: AATreeData): boolean => TRADESKILL_TREE_TYPES.has(tree.tree_type)

/** AA points spent in one tree (rank × cost), optionally excluding a node. */
export function treePoints(tree: AATreeData, alloc: Allocation | undefined, excludeNodeId?: number): number {
  if (!alloc) return 0
  let total = 0
  for (const node of tree.nodes) {
    if (node.node_id === excludeNodeId) continue
    total += rankOf(alloc, node.node_id) * node.pointspertier
  }
  return total
}

/** AA points spent in one classification line of a tree, optionally excluding a node. */
export function linePoints(
  tree: AATreeData,
  alloc: Allocation | undefined,
  classification: string,
  excludeNodeId?: number,
): number {
  if (!alloc || !classification) return 0
  let total = 0
  for (const node of tree.nodes) {
    if (node.node_id === excludeNodeId || node.classification !== classification) continue
    total += rankOf(alloc, node.node_id) * node.pointspertier
  }
  return total
}

/** Points spent across a pool (adventure or tradeskill trees), optionally
 * excluding one node (self-exclusive global thresholds). */
export function poolPoints(
  ctx: PlannerCtx,
  plan: PlanAllocations,
  pool: 'adventure' | 'tradeskill',
  exclude?: { treeId: number; nodeId: number },
): number {
  let total = 0
  for (const tree of ctx.trees) {
    if (isTradeskill(tree) !== (pool === 'tradeskill')) continue
    const excludeNode = exclude && exclude.treeId === tree.tree_id ? exclude.nodeId : undefined
    total += treePoints(tree, plan[String(tree.tree_id)], excludeNode)
  }
  return total
}

/** The unmet unlock requirement for holding ranks in `node`, or null.
 * All counters are self-exclusive (see module docs). */
function unmetRequirement(ctx: PlannerCtx, plan: PlanAllocations, tree: AATreeData, node: AANode): string | null {
  const alloc = plan[String(tree.tree_id)]
  if (node.points_to_unlock > 0 && treePoints(tree, alloc, node.node_id) < node.points_to_unlock) {
    return `Requires ${node.points_to_unlock} points spent in ${tree.tree_name}`
  }
  const clsReq = node.classification_points_required ?? 0
  if (clsReq > 0 && linePoints(tree, alloc, node.classification, node.node_id) < clsReq) {
    return `Requires ${clsReq} points spent in ${node.classification}`
  }
  const globReq = node.points_global_to_unlock ?? 0
  if (globReq > 0 && poolPoints(ctx, plan, 'adventure', { treeId: tree.tree_id, nodeId: node.node_id }) < globReq) {
    return `Requires ${globReq} total AA spent`
  }
  const parentId = node.first_parent_id
  const parentTier = node.first_parent_required_tier ?? 0
  if (parentId != null && parentTier > 0 && rankOf(alloc, parentId) < parentTier) {
    const parent = tree.nodes.find(n => n.node_id === parentId)
    return `Requires ${parentTier} rank${parentTier !== 1 ? 's' : ''} in ${parent?.name ?? `node #${parentId}`}`
  }
  if (tree.tree_type === 'shadows') {
    const shadowsGate = unmetShadowsSection(tree, alloc, node)
    if (shadowsGate) return shadowsGate
  }
  return null
}

/** The shadows structural rule: each section (distinct ycoord row, in order)
 * needs SHADOWS_SECTION_UNLOCK points spent in the section above it. */
function unmetShadowsSection(tree: AATreeData, alloc: Allocation | undefined, node: AANode): string | null {
  const rows = [...new Set(tree.nodes.map(n => n.ycoord))].sort((a, b) => a - b)
  const idx = rows.indexOf(node.ycoord)
  if (idx <= 0) return null
  const prevY = rows[idx - 1]
  let prevPoints = 0
  for (const n of tree.nodes) {
    if (n.ycoord === prevY) prevPoints += rankOf(alloc, n.node_id) * n.pointspertier
  }
  if (prevPoints >= SHADOWS_SECTION_UNLOCK) return null
  const prevName = tree.nodes.find(n => n.ycoord === prevY)?.classification.trim() || 'the previous line'
  return `Requires ${SHADOWS_SECTION_UNLOCK} points spent in ${prevName}`
}

/** Is `node` clickable at rank 0 (used for the locked/greyed styling)? */
export function nodeUnlocked(ctx: PlannerCtx, plan: PlanAllocations, tree: AATreeData, node: AANode): boolean {
  return unmetRequirement(ctx, plan, tree, node) === null
}

export function canAddRank(ctx: PlannerCtx, plan: PlanAllocations, tree: AATreeData, node: AANode): Verdict {
  const alloc = plan[String(tree.tree_id)]
  if (rankOf(alloc, node.node_id) >= node.maxtier) {
    return { ok: false, reason: 'Already at max rank' }
  }
  const unmet = unmetRequirement(ctx, plan, tree, node)
  if (unmet) return { ok: false, reason: unmet }

  const cost = node.pointspertier
  if (treePoints(tree, alloc) + cost > TREE_POINT_CAP) {
    return { ok: false, reason: `Tree cap reached (${TREE_POINT_CAP} points)` }
  }
  const pool = isTradeskill(tree) ? 'tradeskill' : 'adventure'
  const cap = pool === 'tradeskill' ? ctx.tradeskillCap : ctx.aaCap
  if (cap > 0 && poolPoints(ctx, plan, pool) + cost > cap) {
    return { ok: false, reason: pool === 'tradeskill' ? `Tradeskill AA cap reached (${cap})` : `AA cap reached (${cap})` }
  }
  return { ok: true }
}

/** Every taken rank re-checked against the (possibly simulated) allocation. */
export function validatePlan(ctx: PlannerCtx, plan: PlanAllocations): PlanViolation[] {
  const violations: PlanViolation[] = []
  for (const tree of ctx.trees) {
    const alloc = plan[String(tree.tree_id)]
    if (!alloc) continue
    for (const node of tree.nodes) {
      const r = rankOf(alloc, node.node_id)
      if (r <= 0) continue
      if (r > node.maxtier) {
        violations.push({ treeId: tree.tree_id, nodeId: node.node_id, nodeName: node.name, reason: `Rank ${r} exceeds max ${node.maxtier}` })
        continue
      }
      const unmet = unmetRequirement(ctx, plan, tree, node)
      if (unmet) {
        violations.push({ treeId: tree.tree_id, nodeId: node.node_id, nodeName: node.name, reason: unmet })
      }
    }
    if (treePoints(tree, alloc) > TREE_POINT_CAP) {
      violations.push({ treeId: tree.tree_id, nodeId: 0, nodeName: tree.tree_name, reason: `Over the ${TREE_POINT_CAP}-point tree cap` })
    }
  }
  if (ctx.aaCap > 0 && poolPoints(ctx, plan, 'adventure') > ctx.aaCap) {
    violations.push({ treeId: 0, nodeId: 0, nodeName: 'Adventure AAs', reason: `Over the ${ctx.aaCap}-point AA cap` })
  }
  if (ctx.tradeskillCap > 0 && poolPoints(ctx, plan, 'tradeskill') > ctx.tradeskillCap) {
    violations.push({ treeId: 0, nodeId: 0, nodeName: 'Tradeskill AAs', reason: `Over the ${ctx.tradeskillCap}-point tradeskill cap` })
  }
  return violations
}

export function canRemoveRank(ctx: PlannerCtx, plan: PlanAllocations, tree: AATreeData, node: AANode): Verdict {
  if (rankOf(plan[String(tree.tree_id)], node.node_id) <= 0) {
    return { ok: false, reason: 'Nothing spent here' }
  }
  const simulated = withRank(plan, tree.tree_id, node.node_id, rankOf(plan[String(tree.tree_id)], node.node_id) - 1)
  const violations = validatePlan(ctx, simulated)
  if (violations.length > 0) {
    const v = violations[0]
    return { ok: false, reason: `${v.nodeName} would lose its requirement (${v.reason.toLowerCase()})` }
  }
  return { ok: true }
}

/** Immutable rank set — ranks of 0 are dropped so allocations stay sparse. */
export function withRank(plan: PlanAllocations, treeId: number, nodeId: number, rank: number): PlanAllocations {
  const treeKey = String(treeId)
  const nodeKey = String(nodeId)
  const alloc = { ...(plan[treeKey] ?? {}) }
  if (rank <= 0) delete alloc[nodeKey]
  else alloc[nodeKey] = rank
  return { ...plan, [treeKey]: alloc }
}

export function addRank(plan: PlanAllocations, tree: AATreeData, node: AANode): PlanAllocations {
  return withRank(plan, tree.tree_id, node.node_id, rankOf(plan[String(tree.tree_id)], node.node_id) + 1)
}

export function removeRank(plan: PlanAllocations, tree: AATreeData, node: AANode): PlanAllocations {
  return withRank(plan, tree.tree_id, node.node_id, rankOf(plan[String(tree.tree_id)], node.node_id) - 1)
}

/** Seed a plan from the character's live spent maps (CharAATree.spent). */
export function planFromSpent(trees: { tree_id: number; spent: Record<string, number> }[]): PlanAllocations {
  const plan: PlanAllocations = {}
  for (const t of trees) {
    plan[String(t.tree_id)] = { ...t.spent }
  }
  return plan
}

export interface SpendStep {
  tree_id: number
  node_id: number
}

/** A legal purchase order for the whole plan: one step per POINT RANK,
 * replayed greedily through canAddRank so every step is valid at its
 * position (the in-game .aa loader replays purchases in file order).
 * Ranks the rules can never reach (e.g. an off-era import) come back in
 * ``unplaced`` instead of being emitted. */
export function spendOrder(ctx: PlannerCtx, plan: PlanAllocations): { steps: SpendStep[]; unplaced: SpendStep[] } {
  const remaining = new Map<string, number>() // "treeId:nodeId" → ranks left
  for (const tree of ctx.trees) {
    const alloc = plan[String(tree.tree_id)]
    if (!alloc) continue
    for (const node of tree.nodes) {
      const r = Math.min(rankOf(alloc, node.node_id), node.maxtier)
      if (r > 0) remaining.set(`${tree.tree_id}:${node.node_id}`, r)
    }
  }

  // Stable emission order per tree: reading order (row, then column).
  const orderedNodes = ctx.trees.map(tree => ({
    tree,
    nodes: [...tree.nodes].sort((a, b) => a.ycoord - b.ycoord || a.xcoord - b.xcoord),
  }))

  const steps: SpendStep[] = []
  let partial: PlanAllocations = {}
  let progress = true
  while (progress && remaining.size > 0) {
    progress = false
    for (const { tree, nodes } of orderedNodes) {
      for (const node of nodes) {
        const key = `${tree.tree_id}:${node.node_id}`
        // Emit as many consecutive ranks as the rules allow right now —
        // matches the game's own export shape (a node's ranks run together).
        while ((remaining.get(key) ?? 0) > 0 && canAddRank(ctx, partial, tree, node).ok) {
          partial = addRank(partial, tree, node)
          steps.push({ tree_id: tree.tree_id, node_id: node.node_id })
          const left = (remaining.get(key) ?? 0) - 1
          if (left <= 0) remaining.delete(key)
          else remaining.set(key, left)
          progress = true
        }
      }
    }
  }

  const unplaced: SpendStep[] = []
  for (const [key, count] of remaining) {
    const [treeId, nodeId] = key.split(':').map(Number)
    for (let i = 0; i < count; i++) unplaced.push({ tree_id: treeId, node_id: nodeId })
  }
  return { steps, unplaced }
}
