import type { CharAATree } from './CharacterAAsTab'

// Tradeskill AA tree types — counted + capped separately from adventure AAs.
// Mirrors backend _TRADESKILL_TYPES (backend/server/api/aa.py).
export const TRADESKILL_TYPES = new Set(['tradeskill', 'tradeskill_general'])

/** Split per-tree spent points into the adventure vs tradeskill pools. */
export function partitionAASpend(trees: CharAATree[]): { adventure: number; tradeskill: number } {
  let adventure = 0
  let tradeskill = 0
  for (const t of trees) {
    if (TRADESKILL_TYPES.has(t.tree_type)) tradeskill += t.total_spent
    else adventure += t.total_spent
  }
  return { adventure, tradeskill }
}
