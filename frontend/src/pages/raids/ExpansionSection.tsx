/**
 * ExpansionSection — one collapsible expansion block on /raids.
 *
 * Each instance owns its own ``useFetch('/api/raids/zones?expansion=X')`` and
 * ``useFetch('/api/raids/categories?expansion=X')``, which sidesteps the
 * Rules-of-Hooks limit on the parent (where the number of expansions is
 * dynamic).
 *
 * Composition (per expansion):
 *   - Section header (collapsible). Shows zone count and admin trash.
 *   - One lane per category (NULL = "Uncategorised" lane, always first,
 *     not draggable as a header, no visible header label).
 *   - Each lane is a grid of zone cards. Admin sees a drag handle on
 *     every card + on every named lane header.
 *   - Drag a zone within its lane → PUT /api/raids/zones/reorder with the
 *     full lane order, position renumbered 0..N-1.
 *   - Drag a zone to a different lane → single DndContext at the
 *     ExpansionSection level captures the move; handler infers source +
 *     destination lane from active.id / over.id prefix.
 *   - Drag a named-lane header → PUT /api/raids/categories/reorder.
 *   - Admin-only "Add raid zone" button → ZonePickerModal → POST.
 *   - Admin-only "+ Add category" button → inline prompt → POST.
 *   - DungeonsCard (unchanged — contributor-gated internally).
 *
 * Single DndContext pattern for cross-lane zone drags:
 *   - ONE <DndContext onDragEnd={handleDragEnd}> wraps everything.
 *   - handleDragEnd branches on id prefix: 'cat:' → category reorder,
 *     'zone:' → zone reorder / cross-lane move.
 *   - Each lane's zone-grid is a <SortableContext items={zoneIds}>.
 *   - Each lane also has a useDroppable droppable zone-grid container so
 *     empty lanes can accept drops.
 *
 * Mutations refetch via the hook's ``refetch`` and ``onExpansionRemoved``
 * lifts deletes back to the parent so the whole expansion section
 * disappears in one render.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  closestCenter,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

import { Button, Card } from '../../components/ui'
import { fmtRelative } from '../../formatters'
import { useFetch } from '../../hooks/useFetch'
import { type AuthState, isAdmin, useAuth } from '../../hooks/useAuth'
import { type KilledEncounter } from '../../hooks/useRaidProgress'
import { DungeonsCard } from './DungeonsCard'
import { ZonePickerModal } from './ZonePickerModal'
import {
  type Category,
  type Lane,
  type Zone,
  groupZonesByCategory,
} from './types'

// ── DnD timing constants (matches BossRosterEditor) ──────────────────────────
const DND_TOUCH_DELAY_MS = 250
const DND_TOUCH_TOLERANCE_PX = 5

// Stable id prefixes for the two sortable contexts (lanes vs zones). The
// zone id is `zone:<name>` so it's unique across lanes; the category id is
// `cat:<name>`. The lane-drop id is `lane-drop:<name|__null__>`.
const ZONE_ID = (name: string) => `zone:${name}`
const ZONE_ID_PREFIX = 'zone:'
const CAT_ID = (name: string) => `cat:${name}`
const LANE_DROP_ID = (name: string | null) => `lane-drop:${name ?? '__null__'}`

interface Props {
  expansion: { short: string; name: string }
  /** Initial collapsed/expanded state — controlled by the parent so the
   *  page-level "current xpac defaults to open" logic stays centralised. */
  isOpen: boolean
  onToggle: () => void
  isCurrent: boolean
  killedByZone: Record<string, KilledEncounter[]>
  hasGuild: boolean
  /** Called after a successful admin DELETE of this expansion. The parent
   *  refetches the expansion list — the section disappears on next render. */
  onExpansionRemoved: () => void
  /** Optional auth-state override for tests. */
  authOverride?: AuthState
}

export function ExpansionSection({
  expansion,
  isOpen,
  onToggle,
  isCurrent,
  killedByZone,
  hasGuild,
  onExpansionRemoved,
  authOverride,
}: Props) {
  const liveAuth = useAuth()
  const auth = authOverride ?? liveAuth
  const admin = isAdmin(auth)

  // Featured raid zones + categories for this expansion. Hooks fire
  // unconditionally so we never trip Rules of Hooks even when admin
  // toggles state mid-render.
  const zonesFetch = useFetch<Zone[]>(`/api/raids/zones?expansion=${encodeURIComponent(expansion.short)}`)
  const categoriesFetch = useFetch<Category[]>(
    `/api/raids/categories?expansion=${encodeURIComponent(expansion.short)}`,
  )
  const zones: Zone[] = zonesFetch.data ?? []
  const categories: Category[] = categoriesFetch.data ?? []
  const zoneCount = zones.length

  const lanes = groupZonesByCategory(zones, categories)

  const [pickerOpen, setPickerOpen] = useState(false)
  const [mutationError, setMutationError] = useState<string | null>(null)

  // Same sensor wiring as BossRosterEditor — keep behaviour consistent.
  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(TouchSensor, {
      activationConstraint: { delay: DND_TOUCH_DELAY_MS, tolerance: DND_TOUCH_TOLERANCE_PX },
    }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  async function handleAddZone(zoneName: string) {
    setMutationError(null)
    try {
      const r = await fetch(`/api/raids/zones/${encodeURIComponent(zoneName)}`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        setMutationError(body?.detail ?? `Add failed: ${r.status}`)
        return
      }
      setPickerOpen(false)
      zonesFetch.refetch()
      // Categories list is unchanged by an add (new zones land in NULL lane)
      // but we keep them in sync anyway in case the user racing with another
      // admin's edits would otherwise see stale lanes.
      categoriesFetch.refetch()
    } catch (err) {
      setMutationError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handleRemoveZone(zoneName: string) {
    if (!window.confirm(`Remove "${zoneName}" from /raids?\n\nBoss data is preserved — re-add the zone to restore it.`)) return
    setMutationError(null)
    try {
      const r = await fetch(`/api/raids/zones/${encodeURIComponent(zoneName)}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!r.ok) {
        setMutationError(`Remove failed: ${r.status}`)
        return
      }
      zonesFetch.refetch()
    } catch (err) {
      setMutationError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handleRemoveExpansion() {
    if (!window.confirm(
      `Remove "${expansion.name} (${expansion.short})" from /raids?\n\n` +
      `All featured raid zones in this expansion will be removed too. ` +
      `Boss data is preserved — re-add the expansion + zones to restore everything.`,
    )) return
    setMutationError(null)
    try {
      const r = await fetch(`/api/raids/expansions/${encodeURIComponent(expansion.short)}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!r.ok) {
        setMutationError(`Remove failed: ${r.status}`)
        return
      }
      onExpansionRemoved()
    } catch (err) {
      setMutationError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handleAddCategory() {
    const name = window.prompt('Category name (e.g. "Tier 1", "Wing A"):')
    if (!name) return
    const trimmed = name.trim()
    if (!trimmed) return
    setMutationError(null)
    try {
      const r = await fetch(
        `/api/raids/categories?expansion=${encodeURIComponent(expansion.short)}`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: trimmed }),
        },
      )
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        setMutationError(body?.detail ?? `Create category failed: ${r.status}`)
        return
      }
      categoriesFetch.refetch()
    } catch (err) {
      setMutationError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handleDeleteCategory(catName: string) {
    if (!window.confirm(
      `Delete category "${catName}"?\n\nZones in this category will move to Uncategorised.`,
    )) return
    setMutationError(null)
    try {
      const r = await fetch(
        `/api/raids/categories?expansion=${encodeURIComponent(expansion.short)}&name=${encodeURIComponent(catName)}`,
        { method: 'DELETE', credentials: 'include' },
      )
      if (!r.ok) {
        setMutationError(`Delete category failed: ${r.status}`)
        return
      }
      categoriesFetch.refetch()
      zonesFetch.refetch()
    } catch (err) {
      setMutationError(err instanceof Error ? err.message : String(err))
    }
  }

  // ── Drag handlers ───────────────────────────────────────────────────────────

  /** Find the lane that contains a zone by name. Returns null if the zone
   *  isn't in any lane (shouldn't happen — defensive). */
  function laneOfZone(name: string): Lane | null {
    return lanes.find(l => l.zones.some(z => z.name === name)) ?? null
  }

  /** Build the full reorder payload from the current lanes after applying
   *  a move. `from` and `to` are lane references (already mutated copies);
   *  flattened into one ordered list with category/position renumbered. */
  function payloadFromLanes(updated: Lane[]): { name: string; category: string | null; position: number }[] {
    const out: { name: string; category: string | null; position: number }[] = []
    for (const lane of updated) {
      lane.zones.forEach((z, idx) => {
        out.push({ name: z.name, category: lane.name, position: idx })
      })
    }
    return out
  }

  async function reorderZonesRequest(payload: { name: string; category: string | null; position: number }[]) {
    setMutationError(null)
    const r = await fetch('/api/raids/zones/reorder', {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expansion: expansion.short, zones: payload }),
    })
    if (!r.ok) {
      const body = await r.json().catch(() => ({}))
      setMutationError(body?.detail ?? `Reorder failed: ${r.status}`)
      await zonesFetch.refetch()
      return
    }
    await zonesFetch.refetch()
    await categoriesFetch.refetch()
  }

  async function reorderCategoriesRequest(ordering: { name: string; position: number }[]) {
    setMutationError(null)
    const r = await fetch('/api/raids/categories/reorder', {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expansion: expansion.short, categories: ordering }),
    })
    if (!r.ok) {
      const body = await r.json().catch(() => ({}))
      setMutationError(body?.detail ?? `Reorder failed: ${r.status}`)
      await categoriesFetch.refetch()
      return
    }
    await categoriesFetch.refetch()
  }

  /** Zone-level drag. Active id is 'zone:<name>'; over id is either
   *  'zone:<name>' (drop on a zone) or 'lane-drop:<laneName|__null__>'
   *  (drop on an empty-lane placeholder). The destination lane is inferred
   *  from the over id. */
  async function handleZoneDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const activeName = String(active.id).slice(ZONE_ID_PREFIX.length)
    const overId = String(over.id)

    let destLaneName: string | null | undefined = undefined
    let toIdx: number = -1

    if (overId.startsWith(ZONE_ID_PREFIX)) {
      // Dropped on a zone — destination is that zone's lane.
      const overName = overId.slice(ZONE_ID_PREFIX.length)
      const destLane = laneOfZone(overName)
      if (!destLane) return
      destLaneName = destLane.name
      toIdx = destLane.zones.findIndex(z => z.name === overName)
    } else if (overId.startsWith('lane-drop:')) {
      // Dropped on an empty lane's droppable container.
      const rawName = overId.slice('lane-drop:'.length)
      destLaneName = rawName === '__null__' ? null : rawName
      const destLane = lanes.find(l => l.name === destLaneName)
      toIdx = destLane ? destLane.zones.length : 0
    } else {
      return
    }

    const sourceLane = laneOfZone(activeName)
    if (!sourceLane) return
    // destLaneName is null (uncategorised) or a category string; undefined means not resolved.
    if (destLaneName === undefined) return

    // Snapshot lanes into mutable copies.
    const updated: Lane[] = lanes.map(l => ({ ...l, zones: [...l.zones] }))
    const src = updated.find(l => l.name === sourceLane.name)!
    const dst = updated.find(l => l.name === destLaneName)!
    if (!dst) return

    const fromIdx = src.zones.findIndex(z => z.name === activeName)
    if (fromIdx === -1) return
    const [moved] = src.zones.splice(fromIdx, 1)
    // Cross-lane drop or in-lane reorder — insert at the dest index.
    dst.zones.splice(toIdx === -1 ? dst.zones.length : toIdx, 0, { ...moved, category: dst.name })

    await reorderZonesRequest(payloadFromLanes(updated))
  }

  /** Category-lane header drag. ids are 'cat:<name>'. The Uncategorised lane
   *  has no header so its id never appears here. */
  async function handleCategoryDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const activeName = String(active.id).slice('cat:'.length)
    const overName = String(over.id).slice('cat:'.length)
    const ordered = [...categories]
    const fromIdx = ordered.findIndex(c => c.name === activeName)
    const toIdx = ordered.findIndex(c => c.name === overName)
    if (fromIdx === -1 || toIdx === -1) return
    const [moved] = ordered.splice(fromIdx, 1)
    ordered.splice(toIdx, 0, moved)
    await reorderCategoriesRequest(ordered.map((c, idx) => ({ name: c.name, position: idx })))
  }

  /** Unified drag handler: branches on active.id prefix. */
  async function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const activeId = String(active.id)
    if (activeId.startsWith('cat:')) {
      await handleCategoryDragEnd(event)
    } else if (activeId.startsWith(ZONE_ID_PREFIX)) {
      await handleZoneDragEnd(event)
    }
  }

  // The categories drag context's sortable items list = the named lane ids only.
  const categoryIds = categories.map(c => CAT_ID(c.name))

  return (
    <section>
      <div className="flex items-baseline gap-2 mb-1">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={isOpen}
          className="
            group flex-1 flex items-baseline gap-2 text-left cursor-pointer
            appearance-none border-0 bg-transparent p-0
            text-[0.7rem] uppercase tracking-[0.08em] text-gold font-semibold
            hover:text-gold-bright transition-colors
          "
        >
          <span
            aria-hidden
            className="inline-block w-[0.6rem] text-text-muted group-hover:text-gold transition-colors"
          >
            {isOpen ? '▾' : '▸'}
          </span>
          <span>{expansion.name} ({expansion.short})</span>
          {isCurrent && (
            <span className="ml-1 normal-case tracking-normal text-[0.65rem] text-gold-dim font-normal">
              · current
            </span>
          )}
          <span className="ml-auto normal-case tracking-normal text-[0.7rem] text-text-muted font-normal tabular-nums">
            {zoneCount} zone{zoneCount === 1 ? '' : 's'}
          </span>
        </button>
        {admin && (
          <Button
            variant="danger"
            size="icon"
            aria-label={`Remove ${expansion.short} expansion from /raids`}
            title="Remove expansion from /raids"
            onClick={handleRemoveExpansion}
          >
            🗑
          </Button>
        )}
      </div>

      {isOpen && (
        <>
          {zonesFetch.loading && <p className="text-text-muted text-sm">Loading…</p>}
          {zonesFetch.error && (
            <p className="text-danger text-sm">Failed to load raid zones: {zonesFetch.error}</p>
          )}
          {mutationError && <p className="text-danger text-sm mb-2">{mutationError}</p>}

          {!zonesFetch.loading && !zonesFetch.error && zoneCount === 0 && (
            <p className="text-text-muted text-sm mb-2">
              No raid zones added yet
              {admin ? ' — click "Add raid zone" to start curating.' : '.'}
            </p>
          )}

          {zoneCount > 0 && (
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
              <SortableContext items={categoryIds} strategy={verticalListSortingStrategy}>
                <div className="flex flex-col gap-4">
                  {lanes.map(lane => (
                    <LaneSection
                      key={lane.name ?? '__uncategorised__'}
                      lane={lane}
                      admin={admin}
                      killedByZone={killedByZone}
                      hasGuild={hasGuild}
                      onRemoveZone={handleRemoveZone}
                      onDeleteCategory={handleDeleteCategory}
                    />
                  ))}
                </div>
              </SortableContext>
            </DndContext>
          )}

          {admin && (
            <div className="mt-3 flex gap-2 flex-wrap">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => { setMutationError(null); setPickerOpen(true) }}
              >
                + Add raid zone
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={handleAddCategory}
              >
                + Add category
              </Button>
            </div>
          )}

          {/* DungeonsCard is contributor-gated internally — unchanged. */}
          <DungeonsCard expansion={expansion.short} />
        </>
      )}

      {pickerOpen && (
        <ZonePickerModal
          expansion={expansion.short}
          onPick={handleAddZone}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </section>
  )
}

// ── Lane (one category of zones) ─────────────────────────────────────────────

interface LaneProps {
  lane: Lane
  admin: boolean
  killedByZone: Record<string, KilledEncounter[]>
  hasGuild: boolean
  onRemoveZone: (name: string) => void
  onDeleteCategory: (name: string) => void
}

function LaneSection({ lane, admin, killedByZone, hasGuild, onRemoveZone, onDeleteCategory }: LaneProps) {
  const isUncategorised = lane.name === null

  // Make the lane's zone-grid container droppable so empty lanes can still
  // receive a dragged zone. The id uses the lane name so handleZoneDragEnd
  // can identify the destination lane even when dropping on an empty area.
  // Called unconditionally (before any early return) to satisfy rules-of-hooks.
  const { setNodeRef, isOver } = useDroppable({ id: LANE_DROP_ID(lane.name) })

  // The Uncategorised lane has NO header label. Named lanes get a draggable
  // <SortableLaneHeader>. If the uncategorised lane is empty, render nothing.
  if (isUncategorised && lane.zones.length === 0) return null

  return (
    <div>
      {!isUncategorised && (
        <SortableLaneHeader
          name={lane.name!}
          draggable={admin}
          showDelete={admin}
          onDelete={() => onDeleteCategory(lane.name!)}
        />
      )}
      <SortableContext items={lane.zones.map(z => ZONE_ID(z.name))} strategy={verticalListSortingStrategy}>
        <div
          ref={setNodeRef}
          className={`grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 ${isOver ? 'ring-2 ring-gold/40 rounded-md' : ''}`}
        >
          {lane.zones.length === 0 && admin && (
            <div className="col-span-full text-text-muted text-[0.78rem] italic min-h-[4rem] flex items-center">
              {lane.name ? `Drop zones here to add to "${lane.name}".` : ''}
            </div>
          )}
          {lane.zones.map(zone => (
            <SortableZoneCard
              key={zone.name}
              zone={zone}
              killed={killedByZone[zone.name] ?? []}
              hasGuild={hasGuild}
              draggable={admin}
              showRemove={admin}
              onRemove={() => onRemoveZone(zone.name)}
            />
          ))}
        </div>
      </SortableContext>
    </div>
  )
}

function SortableLaneHeader({
  name,
  draggable,
  showDelete,
  onDelete,
}: {
  name: string
  draggable: boolean
  showDelete: boolean
  onDelete: () => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: CAT_ID(name) })
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }
  return (
    <div
      ref={setNodeRef}
      style={style}
      className="flex items-baseline gap-2 mb-1"
    >
      {draggable && (
        <button
          type="button"
          className="appearance-none border-0 bg-transparent text-text-muted cursor-grab active:cursor-grabbing shrink-0 touch-none select-none p-1 -ml-1 rounded hover:text-gold hover:bg-surface-raised text-[0.7rem]"
          aria-label={`Drag category ${name} to reorder`}
          {...attributes}
          {...listeners}
        >
          ⋮⋮
        </button>
      )}
      <div className="text-[0.7rem] uppercase tracking-[0.08em] text-gold font-semibold flex-1">
        {name}
      </div>
      {showDelete && (
        <Button
          variant="danger"
          size="icon"
          aria-label={`Delete category ${name}`}
          title={`Delete category "${name}" (zones move to Uncategorised)`}
          onClick={onDelete}
        >
          🗑
        </Button>
      )}
    </div>
  )
}

// ── SortableZoneCard ──────────────────────────────────────────────────────────

interface ZoneCardProps {
  zone: Zone
  killed: KilledEncounter[]
  hasGuild: boolean
  draggable: boolean
  showRemove: boolean
  onRemove: () => void
}

function SortableZoneCard({ zone, killed, hasGuild, draggable, showRemove, onRemove }: ZoneCardProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: ZONE_ID(zone.name) })
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }
  const total = zone.bosses.length
  const killedCount = killed.length
  const pct = total > 0 ? Math.round((killedCount / total) * 100) : 0
  const lastKillAt = killed.length > 0 ? Math.max(...killed.map(k => k.last_kill_at)) : null

  return (
    <div ref={setNodeRef} style={style} className="relative group">
      {draggable && (
        <button
          type="button"
          className="absolute top-2 left-2 z-10 appearance-none border-0 bg-bg/70 text-text-muted cursor-grab active:cursor-grabbing shrink-0 touch-none select-none px-1.5 py-0.5 rounded hover:text-gold hover:bg-surface-raised text-[0.72rem]"
          aria-label={`Drag ${zone.name} to reorder`}
          {...attributes}
          {...listeners}
        >
          ⋮⋮
        </button>
      )}
      <Link
        to={`/raids/${encodeURIComponent(zone.name)}`}
        className="block no-underline"
      >
        <Card className="h-full transition-colors group-hover:border-gold/60 flex flex-col gap-2">
          <div>
            <div className={`font-heading text-gold-bright text-[1.05rem] leading-snug mb-1 pr-7 ${draggable ? 'pl-6' : ''}`}>
              {zone.name}
            </div>
            <div className="text-text-muted text-[0.78rem]">
              {total} encounter{total === 1 ? '' : 's'}
              {zone.is_contested && <span className="ml-2 text-gold-dim">· Contested</span>}
            </div>
          </div>
          {hasGuild && (
            <ProgressBar killed={killedCount} total={total} pct={pct} lastKillAt={lastKillAt} />
          )}
        </Card>
      </Link>
      {showRemove && (
        <div className="absolute top-2 right-2">
          <Button
            variant="danger"
            size="icon"
            aria-label={`Remove ${zone.name} from /raids`}
            title="Remove from /raids (boss data preserved)"
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              onRemove()
            }}
          >
            🗑
          </Button>
        </div>
      )}
    </div>
  )
}

interface ProgressBarProps {
  killed: number
  total: number
  pct: number
  lastKillAt: number | null
}

function ProgressBar({ killed, total, pct, lastKillAt }: ProgressBarProps) {
  const isComplete = killed === total && total > 0
  return (
    <div className="mt-auto pt-1">
      <div className="flex items-center justify-between text-[0.7rem] mb-1">
        <span className="uppercase tracking-[0.06em] text-text-muted">Progress</span>
        <span className={isComplete ? 'text-success font-semibold tabular-nums' : 'text-text tabular-nums'}>
          {killed} / {total}
        </span>
      </div>
      <div
        className="h-1.5 rounded-full bg-bg/60 border border-border overflow-hidden"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={killed}
      >
        <div
          className={`h-full transition-[width] duration-300 ${isComplete ? 'bg-success/70' : 'bg-gold/70'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {lastKillAt !== null && (
        <div className="text-[0.68rem] text-text-muted mt-1 text-right">
          Last kill {fmtRelative(lastKillAt)}
        </div>
      )}
    </div>
  )
}
