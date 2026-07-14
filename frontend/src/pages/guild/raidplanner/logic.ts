// Pure planner logic — placement moves, group swaps, warnings, next raid
// date. Kept free of React so it's unit-testable (logic.test.ts).

import type { ClassInfo } from '../../../useClasses'
import { GROUPS, SLOTS_PER_GROUP } from './types'
import type { DropTarget, Placement, PlannerData } from './types'

// ── Placement moves ──────────────────────────────────────────────────────────

function without(placements: Placement[], name: string): Placement[] {
  return placements.filter(p => p.character_name.toLowerCase() !== name.toLowerCase())
}

function findAt(placements: Placement[], group: number, slot: number): Placement | undefined {
  return placements.find(p => p.group_num === group && p.slot === slot)
}

export function findPlacement(placements: Placement[], name: string): Placement | undefined {
  return placements.find(p => p.character_name.toLowerCase() === name.toLowerCase())
}

/**
 * Move a character to a drop target, returning the new placements list.
 * Dropping onto an occupied slot SWAPS: the occupant takes the mover's old
 * spot (or the bench if the mover came from the bench/sitout).
 */
export function moveCharacter(placements: Placement[], name: string, target: DropTarget): Placement[] {
  const mover = findPlacement(placements, name)
  let next = without(placements, name)

  if (target.kind === 'bench') {
    // Bench = rostered but unplaced: represented by NO placement row.
    return next
  }

  if (target.kind === 'sitout') {
    next = [...next, { character_name: name, group_num: null, slot: null, sitout: true }]
    return next
  }

  const occupant = findAt(next, target.group, target.slot)
  if (occupant) {
    next = without(next, occupant.character_name)
    if (mover && mover.group_num !== null && mover.slot !== null) {
      // swap into the mover's old slot
      next = [
        ...next,
        { character_name: occupant.character_name, group_num: mover.group_num, slot: mover.slot, sitout: false },
      ]
    } else if (mover?.sitout) {
      next = [...next, { character_name: occupant.character_name, group_num: null, slot: null, sitout: true }]
    }
    // mover came from bench → occupant goes to bench (no row)
  }
  next = [...next, { character_name: name, group_num: target.group, slot: target.slot, sitout: false }]
  return next
}

/** Swap every placement of two groups (drag one group header onto another). */
export function swapGroups(placements: Placement[], groupA: number, groupB: number): Placement[] {
  if (groupA === groupB) return placements
  return placements.map(p => {
    if (p.group_num === groupA) return { ...p, group_num: groupB }
    if (p.group_num === groupB) return { ...p, group_num: groupA }
    return p
  })
}

// ── Derived views ────────────────────────────────────────────────────────────

export interface GridView {
  groups: (Placement | null)[][] // [group 0..3][slot 0..5]
  sitout: Placement[]
  bench: string[] // rostered character names with no placement row
}

export function buildGrid(placements: Placement[], rosteredNames: string[]): GridView {
  const groups: (Placement | null)[][] = Array.from({ length: GROUPS }, () =>
    Array.from({ length: SLOTS_PER_GROUP }, () => null),
  )
  const sitout: Placement[] = []
  const placed = new Set<string>()
  for (const p of placements) {
    placed.add(p.character_name.toLowerCase())
    if (p.sitout) sitout.push(p)
    else if (p.group_num !== null && p.slot !== null) {
      if (p.group_num >= 1 && p.group_num <= GROUPS && p.slot >= 0 && p.slot < SLOTS_PER_GROUP) {
        groups[p.group_num - 1][p.slot] = p
      }
    }
  }
  const bench = rosteredNames.filter(n => !placed.has(n.toLowerCase()))
  return { groups, sitout, bench }
}

// ── Warnings ─────────────────────────────────────────────────────────────────

export interface GroupWarning {
  group: number // 1..4 (0 = raid-wide)
  text: string
  severity: 'warn' | 'info'
}

/**
 * The "useful info" strip. Data-driven off /api/classes:
 *   - every non-empty group should have ≥1 Priest, ≥1 Bard (subclass) and
 *     ≥1 Enchanter (subclass)
 *   - AFK characters placed in groups
 *   - the same player fielding 2+ characters across groups
 */
export function computeWarnings(
  data: Pick<PlannerData, 'availability' | 'players'>,
  placements: Placement[],
  classByChar: Map<string, ClassInfo | undefined>,
): GroupWarning[] {
  const warnings: GroupWarning[] = []
  const inGroups = placements.filter(p => !p.sitout && p.group_num !== null)

  for (let g = 1; g <= GROUPS; g++) {
    const members = inGroups.filter(p => p.group_num === g)
    if (members.length === 0) continue
    const infos = members.map(p => classByChar.get(p.character_name.toLowerCase()))
    const has = (pred: (c: ClassInfo) => boolean) => infos.some(c => c && pred(c))

    if (!has(c => c.archetype === 'Priest')) {
      warnings.push({ group: g, severity: 'warn', text: `Group ${g} has no priest` })
    }
    if (!has(c => c.subclass === 'Bard')) {
      warnings.push({ group: g, severity: 'warn', text: `Group ${g} has no bard` })
    }
    if (!has(c => c.subclass === 'Enchanter')) {
      warnings.push({ group: g, severity: 'warn', text: `Group ${g} has no enchanter` })
    }
    for (const p of members) {
      if (data.availability[p.character_name.toLowerCase()] === 'afk') {
        warnings.push({ group: g, severity: 'warn', text: `${p.character_name} is AFK on this date` })
      }
    }
  }

  // Same player fielding multiple characters in groups.
  const byPlayer = new Map<string, string[]>()
  for (const p of inGroups) {
    const player = data.players[p.character_name.toLowerCase()]
    if (!player) continue
    byPlayer.set(player, [...(byPlayer.get(player) ?? []), p.character_name])
  }
  for (const [player, chars] of byPlayer) {
    if (chars.length > 1) {
      warnings.push({
        group: 0,
        severity: 'info',
        text: `${player} has ${chars.length} characters in the raid (${chars.join(', ')})`,
      })
    }
  }

  return warnings
}

// ── Date helpers ─────────────────────────────────────────────────────────────

/**
 * The next date (today included) that lands on one of the team's raid
 * weekdays. Raid slot days use ISO weekdays 1=Mon..7=Sun. Falls back to
 * today when the team has no raids configured.
 */
export function nextRaidDate(raidDays: number[][], from: Date = new Date()): string {
  const all = new Set(raidDays.flat())
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  if (all.size === 0) return iso(from)
  for (let i = 0; i < 7; i++) {
    const d = new Date(from)
    d.setDate(from.getDate() + i)
    const isoDay = d.getDay() === 0 ? 7 : d.getDay() // JS 0=Sun → ISO 7
    if (all.has(isoDay)) return iso(d)
  }
  return iso(from)
}

/** Serialisable comparison for the debounced-save dirty check. */
export function placementsEqual(a: Placement[], b: Placement[]): boolean {
  const key = (p: Placement) => `${p.character_name.toLowerCase()}|${p.group_num}|${p.slot}|${p.sitout ? 1 : 0}`
  const sa = [...a].map(key).sort()
  const sb = [...b].map(key).sort()
  return sa.length === sb.length && sa.every((v, i) => v === sb[i])
}

// ── Raid-alt ownership ───────────────────────────────────────────────────────

/**
 * Who owns a raid alt, for the "Menwardiir — Menludiir's alt" caption.
 * Resolution: the alt's claiming player (via `players`), then that player's
 * OTHER rostered character — preferring one currently placed in a group,
 * then any raider, then any other roled character. Falls back to the
 * player's display name when they have no other character on the roster.
 * Returns null when the alt has no claim (owner unknown).
 */
export function altOwnerLabel(
  altName: string,
  players: Record<string, string>,
  roster: { name: string; role: string | null }[],
  placements: Placement[],
): string | null {
  const player = players[altName.toLowerCase()]
  if (!player) return null

  const placedLower = new Set(
    placements.filter(p => !p.sitout && p.group_num !== null).map(p => p.character_name.toLowerCase()),
  )
  const siblings = roster.filter(
    r =>
      r.role &&
      r.name.toLowerCase() !== altName.toLowerCase() &&
      players[r.name.toLowerCase()] === player,
  )
  const ranked = [...siblings].sort((a, b) => {
    const score = (r: { name: string; role: string | null }) =>
      (placedLower.has(r.name.toLowerCase()) ? 0 : 2) + (r.role === 'raider' ? 0 : 1)
    return score(a) - score(b)
  })
  return ranked[0]?.name ?? player
}
