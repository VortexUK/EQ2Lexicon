/**
 * BossRosterEditor — drag-reorder, per-boss edit panel, add/remove, sibling-mob management.
 * Rendered inside RaidZonePage behind an "Edit roster" toggle (admins + contributors only).
 *
 * The component is a pure editor layer; it never owns zone data itself.
 * All mutations go to the zones_admin API and call onReload() so the parent
 * re-fetches the authoritative state from the server.
 */
import { useEffect, useRef, useState } from 'react'
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  closestCenter,
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

import { Button, SectionLabel } from './ui'

// ── Types ─────────────────────────────────────────────────────────────────────

export interface EditorMob {
  id: number
  mob_name: string
  position: number
}

export interface EditorEncounter {
  id: number
  encounter_name: string
  position: number
  stage: string | null
  wiki_url: string | null
  mobs: EditorMob[]
}

interface Props {
  zoneName: string
  encounters: EditorEncounter[]
  onReload: () => Promise<void> | void
}

// ── DnD timing constants ──────────────────────────────────────────────────────
const DND_TOUCH_DELAY_MS    = 250
const DND_TOUCH_TOLERANCE_PX = 5

// ── Shared input class (avoids white UA background on raw inputs) ──────────────
const inputCls =
  'w-full appearance-none bg-surface border border-border rounded-md px-3 py-1 text-text outline-none focus:border-gold/60 text-[0.88rem]'

// ── Root component ─────────────────────────────────────────────────────────────

export function BossRosterEditor({ zoneName, encounters, onReload }: Props) {
  const [editingId, setEditingId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [addingBoss, setAddingBoss] = useState(false)

  const base = `/api/zones/${encodeURIComponent(zoneName)}`

  const sensors = useSensors(
    useSensor(PointerSensor),
    // TouchSensor with a 250 ms long-press activation so a tap-and-drag is
    // unambiguously a reorder and a quick swipe still scrolls the page on iOS.
    useSensor(TouchSensor, { activationConstraint: { delay: DND_TOUCH_DELAY_MS, tolerance: DND_TOUCH_TOLERANCE_PX } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  async function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return

    const oldIndex = encounters.findIndex(e => e.id === active.id)
    const newIndex = encounters.findIndex(e => e.id === over.id)
    if (oldIndex === -1 || newIndex === -1) return

    // Build new order by moving the dragged item to its new position.
    const reordered = [...encounters]
    const [moved] = reordered.splice(oldIndex, 1)
    reordered.splice(newIndex, 0, moved)
    const ordered_encounter_ids = reordered.map(e => e.id)

    setError(null)
    const r = await fetch(`${base}/encounters/reorder`, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ordered_encounter_ids }),
    })
    if (!r.ok) {
      setError(`Reorder failed: ${r.status} ${r.statusText}`)
      await onReload()
      return
    }
    await onReload()
  }

  async function handleDelete(enc: EditorEncounter) {
    const confirmed = window.confirm(
      `Delete encounter "${enc.encounter_name}"?\n\n` +
        `This also deletes all ACT triggers, spell timers, and strategy text for this encounter. ` +
        `This action cannot be undone.`,
    )
    if (!confirmed) return
    setError(null)
    const r = await fetch(`${base}/encounters/${enc.id}`, {
      method: 'DELETE',
      credentials: 'include',
    })
    if (!r.ok) {
      setError(`Delete failed: ${r.status} ${r.statusText}`)
      await onReload()
      return
    }
    if (editingId === enc.id) setEditingId(null)
    await onReload()
  }

  return (
    <div className="mt-3">
      <SectionLabel>Edit Roster</SectionLabel>

      {error && (
        <p className="text-danger text-[0.82rem] mt-1 mb-2">{error}</p>
      )}

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={encounters.map(e => e.id)} strategy={verticalListSortingStrategy}>
          <ul className="border border-border rounded-md divide-y divide-border/60 overflow-hidden">
            {encounters.map(enc => (
              <SortableBossRow
                key={enc.id}
                enc={enc}
                zoneName={zoneName}
                isEditing={editingId === enc.id}
                onEdit={() => setEditingId(v => (v === enc.id ? null : enc.id))}
                onDelete={() => handleDelete(enc)}
                onReload={onReload}
                onCloseEdit={() => setEditingId(null)}
              />
            ))}
          </ul>
        </SortableContext>
      </DndContext>

      {/* Add boss */}
      <div className="mt-2">
        {addingBoss ? (
          <AddBossForm
            zoneName={zoneName}
            onCancel={() => setAddingBoss(false)}
            onAdded={async () => {
              setAddingBoss(false)
              await onReload()
            }}
          />
        ) : (
          <Button variant="primary" size="sm" onClick={() => setAddingBoss(true)}>
            + Add boss
          </Button>
        )}
      </div>
    </div>
  )
}

// ── Sortable row ───────────────────────────────────────────────────────────────

interface RowProps {
  enc: EditorEncounter
  zoneName: string
  isEditing: boolean
  onEdit: () => void
  onDelete: () => void
  onReload: () => Promise<void> | void
  onCloseEdit: () => void
}

function SortableBossRow({ enc, zoneName, isEditing, onEdit, onDelete, onReload, onCloseEdit }: RowProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: enc.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <li ref={setNodeRef} style={style} className="bg-surface-raised/20">
      <div className="flex items-center gap-2 px-3 py-2">
        {/* Drag handle */}
        <button
          type="button"
          className="appearance-none border-0 bg-transparent text-text-muted cursor-grab active:cursor-grabbing shrink-0 touch-none select-none p-1 -ml-1 rounded hover:text-gold hover:bg-surface-raised"
          aria-label="Drag to reorder"
          {...attributes}
          {...listeners}
        >
          ⋮⋮
        </button>

        {/* Boss name */}
        <span className="font-heading text-gold flex-1 min-w-0 truncate text-[0.95rem]">
          {enc.encounter_name}
        </span>

        {/* Sibling badge */}
        {enc.mobs.length > 1 && (
          <span className="text-[0.68rem] px-1.5 py-[1px] rounded-sm border border-border bg-bg/60 text-text-muted shrink-0">
            +{enc.mobs.length - 1} mob{enc.mobs.length > 2 ? 's' : ''}
          </span>
        )}

        <Button size="sm" variant="ghost" onClick={onEdit} style={{ minWidth: '3.5rem' }}>
          {isEditing ? 'Close' : 'Edit'}
        </Button>
        <Button size="sm" variant="danger" onClick={onDelete}>
          Delete
        </Button>
      </div>

      {isEditing && (
        <BossEditPanel
          enc={enc}
          zoneName={zoneName}
          onReload={onReload}
          onClose={onCloseEdit}
        />
      )}
    </li>
  )
}

// ── Edit panel ─────────────────────────────────────────────────────────────────

interface EditPanelProps {
  enc: EditorEncounter
  zoneName: string
  onReload: () => Promise<void> | void
  onClose: () => void
}

interface EncounterDraft {
  primary_mob: string
  stage: string
  wiki_url: string
}

function BossEditPanel({ enc, zoneName, onReload, onClose }: EditPanelProps) {
  const [draft, setDraft] = useState<EncounterDraft>({
    primary_mob: enc.encounter_name,
    stage: enc.stage ?? '',
    wiki_url: enc.wiki_url ?? '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [addSiblingName, setAddSiblingName] = useState('')
  const [addingSibling, setAddingSibling] = useState(false)

  const base = `/api/zones/${encodeURIComponent(zoneName)}/encounters/${enc.id}`

  // Only send fields that changed from the original value.
  async function saveEncounter() {
    if (!draft.primary_mob.trim()) {
      setError('Boss name is required.')
      return
    }
    setSaving(true)
    setError(null)

    const body: Record<string, string | null> = {}
    if (draft.primary_mob.trim() !== enc.encounter_name) {
      body.primary_mob = draft.primary_mob.trim()
    }
    const stageVal = draft.stage.trim() || null
    if (stageVal !== enc.stage) {
      body.stage = stageVal
    }
    const wikiVal = draft.wiki_url.trim() || null
    if (wikiVal !== enc.wiki_url) {
      body.wiki_url = wikiVal
    }

    if (Object.keys(body).length === 0) {
      setSaving(false)
      onClose()
      return
    }

    try {
      const r = await fetch(`/api/zones/${encodeURIComponent(zoneName)}/encounters/${enc.id}`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
      await onReload()
      onClose()
    } catch (e) {
      setError(String((e as Error).message ?? e))
    } finally {
      setSaving(false)
    }
  }

  async function addSibling() {
    if (!addSiblingName.trim()) return
    setError(null)
    try {
      const r = await fetch(`${base}/mobs`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mob_name: addSiblingName.trim() }),
      })
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
      setAddSiblingName('')
      setAddingSibling(false)
      await onReload()
    } catch (e) {
      setError(String((e as Error).message ?? e))
    }
  }

  async function renameMob(mob: EditorMob, newName: string) {
    if (!newName.trim() || newName.trim() === mob.mob_name) return
    setError(null)
    try {
      const r = await fetch(`${base}/mobs/${mob.id}`, {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mob_name: newName.trim() }),
      })
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
      await onReload()
    } catch (e) {
      setError(String((e as Error).message ?? e))
    }
  }

  async function promoteMob(mob: EditorMob) {
    setError(null)
    try {
      const r = await fetch(`${base}/mobs/${mob.id}/promote`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
      await onReload()
    } catch (e) {
      setError(String((e as Error).message ?? e))
    }
  }

  async function deleteMob(mob: EditorMob) {
    if (enc.mobs.length <= 1) {
      setError('Cannot delete the last mob of an encounter.')
      return
    }
    if (mob.position === 0) {
      setError('Cannot delete the primary mob while siblings exist. Promote a sibling first.')
      return
    }
    const confirmed = window.confirm(`Remove "${mob.mob_name}" from this encounter?`)
    if (!confirmed) return
    setError(null)
    try {
      const r = await fetch(`${base}/mobs/${mob.id}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!r.ok) {
        let detail = `${r.status} ${r.statusText}`
        try {
          const body = await r.json()
          if (body?.detail) detail = body.detail
        } catch { /* non-JSON */ }
        throw new Error(detail)
      }
      await onReload()
    } catch (e) {
      setError(String((e as Error).message ?? e))
    }
  }

  return (
    <div className="px-4 py-3 bg-bg/40 border-t border-border/60 flex flex-col gap-3">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <EditField label="Primary mob / boss name">
          <input
            type="text"
            value={draft.primary_mob}
            onChange={e => setDraft({ ...draft, primary_mob: e.target.value })}
            className={inputCls}
          />
        </EditField>
        <EditField label="Stage (optional)">
          <input
            type="text"
            value={draft.stage}
            onChange={e => setDraft({ ...draft, stage: e.target.value })}
            placeholder="e.g. Wing 1, First Floor"
            className={inputCls}
          />
        </EditField>
        <EditField label="Wiki URL (optional)" className="sm:col-span-2">
          <input
            type="url"
            value={draft.wiki_url}
            onChange={e => setDraft({ ...draft, wiki_url: e.target.value })}
            placeholder="https://eq2.fandom.com/…"
            className={inputCls}
          />
        </EditField>
      </div>

      {/* Sibling mobs */}
      {enc.mobs.length > 0 && (
        <div>
          <span className="text-gold-dim uppercase tracking-[0.08em] text-[0.7rem]">
            Encounter mobs ({enc.mobs.length})
          </span>
          <ul className="mt-1 flex flex-col gap-1">
            {enc.mobs.map(mob => (
              <MobRow
                key={mob.id}
                mob={mob}
                isPrimary={mob.position === 0}
                canDelete={enc.mobs.length > 1 && mob.position !== 0}
                canPromote={mob.position !== 0}
                onRename={newName => renameMob(mob, newName)}
                onPromote={() => promoteMob(mob)}
                onDelete={() => deleteMob(mob)}
              />
            ))}
          </ul>
        </div>
      )}

      {/* Add sibling */}
      {addingSibling ? (
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={addSiblingName}
            onChange={e => setAddSiblingName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') addSibling() }}
            placeholder="Sibling mob name"
            className={inputCls}
            autoFocus
          />
          <Button size="sm" variant="primary" onClick={addSibling} disabled={!addSiblingName.trim()}>
            Add
          </Button>
          <Button size="sm" variant="ghost" onClick={() => { setAddingSibling(false); setAddSiblingName('') }}>
            Cancel
          </Button>
        </div>
      ) : (
        <Button size="sm" variant="secondary" onClick={() => setAddingSibling(true)}>
          + Add sibling mob
        </Button>
      )}

      {error && <p className="text-danger text-[0.82rem]">{error}</p>}

      <div className="flex items-center gap-2 justify-end mt-1 border-t border-border/40 pt-2">
        <Button size="sm" variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
        <Button
          size="sm"
          variant="primary"
          onClick={saveEncounter}
          disabled={saving || !draft.primary_mob.trim()}
        >
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  )
}

// ── Mob row (inside edit panel) ────────────────────────────────────────────────

interface MobRowProps {
  mob: EditorMob
  isPrimary: boolean
  canDelete: boolean
  canPromote: boolean
  onRename: (newName: string) => void
  onPromote: () => void
  onDelete: () => void
}

function MobRow({ mob, isPrimary, canDelete, canPromote, onRename, onPromote, onDelete }: MobRowProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(mob.mob_name)
  const inputRef = useRef<HTMLInputElement>(null)

  // Keep draft in sync if the parent reloads with a renamed mob.
  useEffect(() => {
    setDraft(mob.mob_name)
  }, [mob.mob_name])

  function commit() {
    if (draft.trim() && draft.trim() !== mob.mob_name) {
      onRename(draft.trim())
    }
    setEditing(false)
  }

  return (
    <li className="flex items-center gap-2 bg-surface-raised/30 rounded-sm px-2 py-1">
      {isPrimary && (
        <span className="text-[0.65rem] text-gold-dim uppercase tracking-[0.06em] shrink-0">
          primary
        </span>
      )}
      {editing ? (
        <input
          ref={inputRef}
          type="text"
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { setDraft(mob.mob_name); setEditing(false) } }}
          className={inputCls + ' flex-1'}
          autoFocus
        />
      ) : (
        <span
          className="flex-1 min-w-0 text-[0.85rem] text-text truncate cursor-text"
          onDoubleClick={() => setEditing(true)}
          title="Double-click to rename"
        >
          {mob.mob_name}
        </span>
      )}
      <div className="flex items-center gap-1 shrink-0">
        <button
          type="button"
          className="appearance-none border-0 bg-transparent text-[0.72rem] text-gold-dim hover:text-gold px-1"
          onClick={() => { setEditing(true); /* 0 ms: defer focus until after the input is attached to the DOM */ setTimeout(() => inputRef.current?.focus(), 0) }}
          title="Rename"
        >
          Rename
        </button>
        {canPromote && (
          <button
            type="button"
            className="appearance-none border-0 bg-transparent text-[0.72rem] text-text-muted hover:text-gold px-1"
            onClick={onPromote}
            title="Make primary mob"
          >
            Promote
          </button>
        )}
        {canDelete && (
          <button
            type="button"
            className="appearance-none border-0 bg-transparent text-[0.72rem] text-danger/70 hover:text-danger px-1"
            onClick={onDelete}
            title="Remove from encounter"
          >
            Remove
          </button>
        )}
      </div>
    </li>
  )
}

// ── Add boss form ──────────────────────────────────────────────────────────────

interface AddBossFormProps {
  zoneName: string
  onCancel: () => void
  onAdded: () => Promise<void>
}

function AddBossForm({ zoneName, onCancel, onAdded }: AddBossFormProps) {
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    if (!name.trim()) return
    setSaving(true)
    setError(null)
    try {
      const r = await fetch(`/api/zones/${encodeURIComponent(zoneName)}/encounters`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ primary_mob: name.trim() }),
      })
      if (!r.ok) {
        let detail = `${r.status} ${r.statusText}`
        try {
          const body = await r.json()
          if (body?.detail) detail = body.detail
        } catch { /* non-JSON */ }
        throw new Error(detail)
      }
      await onAdded()
    } catch (e) {
      setError(String((e as Error).message ?? e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mt-2 border border-border rounded-md px-3 py-2 bg-bg/40 flex flex-col gap-2">
      <span className="font-heading text-gold text-[0.92rem]">Add encounter</span>
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') submit() }}
          placeholder="Primary mob / boss name"
          className={inputCls}
          autoFocus
        />
        <Button size="sm" variant="primary" onClick={submit} disabled={saving || !name.trim()}>
          {saving ? 'Adding…' : 'Add'}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
      </div>
      {error && <p className="text-danger text-[0.82rem]">{error}</p>}
    </div>
  )
}

// ── Tiny label sub-primitive ───────────────────────────────────────────────────

function EditField({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={['flex flex-col gap-1', className].filter(Boolean).join(' ')}>
      <span className="text-gold-dim uppercase tracking-[0.08em] text-[0.7rem]">{label}</span>
      {children}
    </label>
  )
}
