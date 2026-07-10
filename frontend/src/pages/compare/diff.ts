/**
 * diff — pure comparison functions for the character compare page.
 *
 * Convention everywhere: Δ = B − A. Positive deltas are rendered success,
 * negative danger, zero muted; a null delta means one side lacks the value
 * (the present side is still shown — data is never hidden).
 *
 * No React, no fetch — everything here is unit-testable in isolation.
 */
import {
  type Character,
  type CharacterStats,
  type EquipmentSlot,
  type Fmt,
  CONSUMABLE_SLOTS,
  LEFT_SLOTS,
  RIGHT_SLOTS,
  STAT_GROUPS,
  WEAPON_SLOTS,
  buildSlotMap,
  fmtStat,
} from '../characterSheet'
import type { CharAATree } from '../CharacterAAsTab'
import type { AATreeData } from '../../components/AATree'

// ── Stats ────────────────────────────────────────────────────────────────────

export interface StatDiffRow {
  label: string
  fmt: Fmt
  a: number | null
  b: number | null
  /** B − A; null when either side is missing the stat. */
  delta: number | null
}

export interface StatDiffGroup {
  title: string
  rows: StatDiffRow[]
}

/** Diff two stat blocks along the shared STAT_GROUPS config. Rows where BOTH
 * sides are null are omitted; one-sided rows keep the present value with a
 * null delta. The composite Weapon group is appended as derived numeric rows
 * (average hit + delay per equipped weapon slot). */
export function diffStats(a: CharacterStats, b: CharacterStats): StatDiffGroup[] {
  const groups: StatDiffGroup[] = []
  for (const group of STAT_GROUPS) {
    const rows: StatDiffRow[] = []
    for (const def of group.rows) {
      const av = a[def.key]
      const bv = b[def.key]
      if (av == null && bv == null) continue
      rows.push({
        label: def.label,
        fmt: def.fmt,
        a: av,
        b: bv,
        delta: av != null && bv != null ? bv - av : null,
      })
    }
    if (rows.length > 0) groups.push({ title: group.title, rows })
  }

  const weaponRows: StatDiffRow[] = []
  for (const w of WEAPON_SLOTS) {
    const avg = (s: CharacterStats): number | null => {
      const min = s[w.min]
      const max = s[w.max]
      return min != null && max != null ? (min + max) / 2 : null
    }
    const aAvg = avg(a)
    const bAvg = avg(b)
    if (aAvg != null || bAvg != null) {
      weaponRows.push({
        label: `${w.label} avg hit`,
        fmt: 'int',
        a: aAvg,
        b: bAvg,
        delta: aAvg != null && bAvg != null ? bAvg - aAvg : null,
      })
      const aDelay = a[w.delay]
      const bDelay = b[w.delay]
      if (aDelay != null || bDelay != null) {
        weaponRows.push({
          label: `${w.label} delay`,
          fmt: 'dec1',
          a: aDelay,
          b: bDelay,
          delta: aDelay != null && bDelay != null ? bDelay - aDelay : null,
        })
      }
    }
  }
  if (weaponRows.length > 0) groups.push({ title: 'Weapon', rows: weaponRows })

  return groups
}

/** Format a delta with an explicit sign (U+2212 minus for negatives). */
export function fmtDelta(delta: number, fmt?: Fmt): string {
  const magnitude = fmtStat(Math.abs(delta), fmt)
  if (delta === 0) return `±${magnitude}`
  return delta > 0 ? `+${magnitude}` : `−${magnitude}`
}

// ── Gear ─────────────────────────────────────────────────────────────────────

export interface AdornFill {
  filled: number
  total: number
}

export interface GearDiffRow {
  slotKey: string
  label: string
  a: EquipmentSlot | null
  b: EquipmentSlot | null
  /** True when both sides wear the same (non-null) item. */
  identical: boolean
  adornsA: AdornFill | null
  adornsB: AdornFill | null
}

export interface GearDiff {
  left: GearDiffRow[]
  right: GearDiffRow[]
  consumables: GearDiffRow[]
  /** Slots (excl. consumables) where the two characters differ. */
  differingCount: number
  /** Slots (excl. consumables) where at least one side has an item. */
  occupiedCount: number
}

function adornFill(slot: EquipmentSlot | null): AdornFill | null {
  if (!slot || slot.adorn_slots.length === 0) return null
  return {
    filled: slot.adorn_slots.filter(a => a.adorn_name != null).length,
    total: slot.adorn_slots.length,
  }
}

/** Align two equipment lists on the canonical paperdoll slots. */
export function diffGear(aEquip: EquipmentSlot[], bEquip: EquipmentSlot[]): GearDiff {
  const mapA = buildSlotMap(aEquip)
  const mapB = buildSlotMap(bEquip)

  const build = (slots: [string, string][]): GearDiffRow[] =>
    slots.map(([label, key]) => {
      const a = mapA.get(key) ?? null
      const b = mapB.get(key) ?? null
      return {
        slotKey: key,
        label,
        a,
        b,
        identical: a?.item_id != null && a.item_id === b?.item_id,
        adornsA: adornFill(a),
        adornsB: adornFill(b),
      }
    })

  const left = build(LEFT_SLOTS)
  const right = build(RIGHT_SLOTS)
  const consumables = build(CONSUMABLE_SLOTS)
  const gearRows = [...left, ...right]
  return {
    left,
    right,
    consumables,
    differingCount: gearRows.filter(r => !r.identical && (r.a !== null || r.b !== null)).length,
    occupiedCount: gearRows.filter(r => r.a !== null || r.b !== null).length,
  }
}

export function nullableDelta(a: number | null, b: number | null): number | null {
  return a != null && b != null ? b - a : null
}

// ── AAs ──────────────────────────────────────────────────────────────────────

/** AA comparison is only meaningful within one subclass (trees are class-
 * specific). Null classes never match. */
export function sameSubclass(a: Pick<Character, 'cls'>, b: Pick<Character, 'cls'>): boolean {
  return a.cls != null && b.cls != null && a.cls === b.cls
}

export interface TreeSummaryDiff {
  tree_id: number
  tree_name: string
  tree_type: string
  spentA: number
  spentB: number
  delta: number
  /** Set when the tree appears in only one character's response. */
  onlyOn: 'a' | 'b' | null
}

/** Union the two characters' visible trees by tree_id — A's order first, then
 * B-only trees appended. A missing side reads as 0 spent. */
export function alignTrees(treesA: CharAATree[], treesB: CharAATree[]): TreeSummaryDiff[] {
  const byIdB = new Map(treesB.map(t => [t.tree_id, t]))
  const seen = new Set<number>()
  const out: TreeSummaryDiff[] = []

  for (const ta of treesA) {
    const tb = byIdB.get(ta.tree_id)
    seen.add(ta.tree_id)
    out.push({
      tree_id: ta.tree_id,
      tree_name: ta.tree_name,
      tree_type: ta.tree_type,
      spentA: ta.total_spent,
      spentB: tb?.total_spent ?? 0,
      delta: (tb?.total_spent ?? 0) - ta.total_spent,
      onlyOn: tb ? null : 'a',
    })
  }
  for (const tb of treesB) {
    if (seen.has(tb.tree_id)) continue
    out.push({
      tree_id: tb.tree_id,
      tree_name: tb.tree_name,
      tree_type: tb.tree_type,
      spentA: 0,
      spentB: tb.total_spent,
      delta: tb.total_spent,
      onlyOn: 'b',
    })
  }
  return out
}

export interface AANodeDiff {
  node_id: number
  name: string
  icon_id: number | null
  maxtier: number
  rankA: number
  rankB: number
  delta: number
}

export interface TreeNodeDiff {
  /** Every node either character has spent points in, tree reading order. */
  rows: AANodeDiff[]
  /** How many of those rows have differing ranks. */
  differing: number
}

/** Node-by-node diff of one tree: the key-union of both spend dicts joined
 * against the tree's node metadata. Nodes neither character spent are omitted
 * by construction. Unknown node ids (metadata drift) degrade to a
 * "Node #<id>" fallback rather than being dropped. */
export function diffTreeNodes(
  spentA: Record<string, number> | undefined,
  spentB: Record<string, number> | undefined,
  tree: AATreeData | null,
): TreeNodeDiff {
  const nodeById = new Map((tree?.nodes ?? []).map(n => [n.node_id, n]))
  const ids = new Set<number>()
  for (const k of Object.keys(spentA ?? {})) ids.add(Number(k))
  for (const k of Object.keys(spentB ?? {})) ids.add(Number(k))

  const rows: AANodeDiff[] = []
  for (const id of ids) {
    const meta = nodeById.get(id)
    const rankA = spentA?.[String(id)] ?? 0
    const rankB = spentB?.[String(id)] ?? 0
    rows.push({
      node_id: id,
      name: meta?.name ?? `Node #${id}`,
      icon_id: meta?.icon_id ?? null,
      maxtier: meta?.maxtier ?? 0,
      rankA,
      rankB,
      delta: rankB - rankA,
    })
  }

  // Tree reading order: top-to-bottom, left-to-right; unknown nodes last.
  rows.sort((x, y) => {
    const mx = nodeById.get(x.node_id)
    const my = nodeById.get(y.node_id)
    if (!mx && !my) return x.node_id - y.node_id
    if (!mx) return 1
    if (!my) return -1
    return mx.ycoord - my.ycoord || mx.xcoord - my.xcoord || x.name.localeCompare(y.name)
  })

  return { rows, differing: rows.filter(r => r.delta !== 0).length }
}
