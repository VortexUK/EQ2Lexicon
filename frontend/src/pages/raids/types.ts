/**
 * Shared types for the raid-zone pages.
 *
 * Both ``RaidZonesPage.tsx`` (the index/grid) and ``RaidZonePage.tsx``
 * (per-zone detail) consume the ``GET /api/zones`` shape and previously
 * defined identical ``EncounterMob`` / ``Encounter`` / ``Zone`` interfaces
 * inline — flagged by the user after the 2026-05-29 frontend cleanliness
 * audit missed it.
 *
 * Per the audit's file-split convention (see ``CLAUDE.md`` →
 * "File-split conventions"), shared types for a page family live in a
 * sibling ``types.ts``.  This is the home for them.
 */

export interface EncounterMob {
  id: number
  mob_name: string
  position: number
}

export interface Encounter {
  id: number
  encounter_name: string
  position: number
  stage: string | null
  wiki_url: string | null
  mobs: EncounterMob[]
}

export interface Zone {
  name: string
  expansion_short: string
  expansion_name: string
  expansion_year: number | null
  types: string[]
  aliases: string[]
  wiki_url: string | null
  is_contested: boolean
  is_instance: boolean
  is_openworld: boolean
  bosses: Encounter[]
  /** Drag-reorder position within the zone's category lane. Present only
   *  on the /api/raids/zones featured-list response (server-curated). */
  position?: number
  /** Named lane this zone belongs to; null = the implicit "Uncategorised"
   *  lane that's always pinned at the top of an expansion. */
  category?: string | null
}

export interface ZoneListResponse {
  expansion: string | null
  type: string | null
  zones: Zone[]
}

/** One admin-defined category (lane) under an expansion. NULL-category
 *  zones live in an implicit "Uncategorised" lane pinned to the top — it
 *  is NOT represented here. */
export interface Category {
  name: string
  position: number
}

/** A lane in the rendered ExpansionSection. `name === null` is the implicit
 *  "Uncategorised" lane (rendered first, no draggable header). `position`
 *  is informational for category lanes — Uncategorised uses a sentinel. */
export interface Lane {
  name: string | null
  position: number
  zones: Zone[]
}

/** Sentinel position so the Uncategorised lane sorts before any real
 *  category (whose positions start at 0). */
export const UNCATEGORISED_POSITION = -1

export function groupZonesByCategory(zones: Zone[], categories: Category[]): Lane[] {
  const byName = new Map<string, Zone[]>()
  const uncategorised: Zone[] = []
  for (const z of zones) {
    if (z.category == null) uncategorised.push(z)
    else {
      const arr = byName.get(z.category) ?? []
      arr.push(z)
      byName.set(z.category, arr)
    }
  }
  // Sort zones within each lane by position (server already returns this
  // order, but defensive re-sort makes the helper safe to call on any
  // stale-cached zones list).
  const posOf = (z: Zone) => z.position ?? 0
  uncategorised.sort((a, b) => posOf(a) - posOf(b))
  for (const arr of byName.values()) arr.sort((a, b) => posOf(a) - posOf(b))

  const lanes: Lane[] = []
  // The Uncategorised lane is ALWAYS rendered (even when empty) for admins
  // so they have a drop target to move zones back to NULL. Non-admin views
  // get to elide it when empty, but the caller can filter that downstream.
  lanes.push({ name: null, position: UNCATEGORISED_POSITION, zones: uncategorised })
  for (const cat of categories) {
    lanes.push({ name: cat.name, position: cat.position, zones: byName.get(cat.name) ?? [] })
  }
  // Defend against a race: any category present in zones but not yet in
  // the categories list (shouldn't happen post auto-create, but a
  // simultaneous tab refetching mid-flight could see this).
  for (const [name, zs] of byName.entries()) {
    if (!categories.some(c => c.name === name)) {
      lanes.push({ name, position: Number.MAX_SAFE_INTEGER, zones: zs })
    }
  }
  return lanes
}
