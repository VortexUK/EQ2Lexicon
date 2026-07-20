/**
 * aaFile — export a plan as the game's own `.aa` spec file.
 *
 * The in-game AA window's Save/Load writes and reads this format: one
 * `<alternateadvancement>` element PER POINT SPENT (node id repeated per
 * rank) in purchase order, bucketed into `<alternateadvancements typenum=N>`
 * sections. The game replays the file in order, so we emit through the
 * engine's spendOrder — every line is legal at its position.
 *
 * typenum buckets were reverse-engineered from a real in-game export
 * (scripts/dev/Menludiir_templar_dps.aa): class/subclass/shadows share
 * bucket 0 (heroic assumed 0 with them — same AA window tab), tradeskill
 * is 3, tradeskill-general 4. Bucket 2 appears empty in game exports and
 * is mirrored for shape-compatibility.
 */

import type { PlanAllocations, PlannerCtx } from './engine'
import { spendOrder } from './engine'

export const TYPENUM_BY_TREE_TYPE: Record<string, number> = {
  class: 0,
  subclass: 0,
  shadows: 0,
  heroic: 0,
  warder: 1,
  prestige: 2,
  tradeskill: 3,
  tradeskill_general: 4,
}

export interface AAFileResult {
  xml: string
  /** Point ranks that could not be legally ordered (off-era imports etc.) —
   * excluded from the file so the in-game load never jams. */
  unplacedCount: number
}

export function buildAAFileXml(ctx: PlannerCtx, plan: PlanAllocations): AAFileResult {
  const { steps, unplaced } = spendOrder(ctx, plan)
  const typeByTree = new Map(ctx.trees.map(t => [t.tree_id, TYPENUM_BY_TREE_TYPE[t.tree_type] ?? 0]))

  // Bucket the ordered steps; per-bucket order counters restart at 1
  // (matches the game's own export).
  const buckets = new Map<number, string[]>()
  for (const step of steps) {
    const typenum = typeByTree.get(step.tree_id) ?? 0
    const lines = buckets.get(typenum) ?? []
    lines.push(
      `    <alternateadvancement id="${step.node_id}" order="${lines.length + 1}" treeID="${step.tree_id}"/>`,
    )
    buckets.set(typenum, lines)
  }
  if (!buckets.has(2)) buckets.set(2, []) // the game always emits an (empty) bucket 2

  const sections = [...buckets.keys()]
    .sort((a, b) => a - b)
    .map(typenum => {
      const lines = buckets.get(typenum) ?? []
      if (lines.length === 0) return `  <alternateadvancements typenum="${typenum}"/>`
      return `  <alternateadvancements typenum="${typenum}">\n${lines.join('\n')}\n  </alternateadvancements>`
    })

  const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<aa game="eq2">\n${sections.join('\n')}\n</aa>\n`
  return { xml, unplacedCount: unplaced.length }
}

/** Trigger a browser download of the spec file. */
export function downloadAAFile(filename: string, xml: string): void {
  const blob = new Blob([xml], { type: 'application/xml' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** "Menludiir" + "Raid DPS!" → "Menludiir_Raid_DPS.aa" */
export function aaFileName(charName: string, planName: string): string {
  const safe = `${charName}_${planName}`.replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '')
  return `${safe || 'aa_plan'}.aa`
}
