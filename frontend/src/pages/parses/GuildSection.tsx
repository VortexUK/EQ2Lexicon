/**
 * GuildSection — one collapsible card per guild in the parses list.
 *
 * Renders three CategorySection children (Raid / Dungeon / Other), each
 * independently collapsible. Officers / admins who can delete every visible
 * row get a header trash button that deletes exactly the parses currently
 * shown for the guild — the rendered bucket, so the page's size / search /
 * bosses-only filters and pagination are honoured by construction.
 */
import { useState } from 'react'

import { Card } from '../../components/ui'
import Caret from '../../components/Caret'
import { toErrorMessage } from '../../lib/errors'

import { CategorySection } from './CategorySection'
import { deleteFightsChunked } from './api'
import { NO_GUILD } from './types'
import type { GuildBucket, ParseEncounterSummary } from './types'

const headerBtnCls = 'flex items-center gap-2 w-full bg-transparent border-none text-inherit cursor-pointer py-2 px-3 text-left font-inherit'

export interface GuildSectionProps {
  bucket: GuildBucket
  defaultExpanded: boolean
  onDeleted: (pred: (e: ParseEncounterSummary) => boolean) => void
  /** Called when a delete could not be fully confirmed — the caller should
   *  refetch so the list matches the database again. */
  onResync?: () => void
}

const CATEGORIES = ['raid', 'dungeon', 'other'] as const

// One section per guild. Renders three CategorySection children (Raid /
// Dungeon / Other), each independently collapsible. Empty categories
// render nothing.
export function GuildSection({ bucket, defaultExpanded, onDeleted, onResync }: GuildSectionProps) {
  const [open, setOpen] = useState(defaultExpanded)
  const [deleting, setDeleting] = useState(false)
  // Every fight currently rendered under this guild (all categories, all
  // zone-days) — this is the exact scope of the header delete.
  const visibleFights = CATEGORIES.flatMap(k =>
    bucket.fightsByCategory[k].flatMap(zd => zd.fights),
  )
  const totalUploads = visibleFights.reduce((n, f) => n + f.uploads.length, 0)
  // Officers / admins get the header delete only when they have delete
  // perms on every visible row for it (admins always do; officers only
  // within their own guild).
  const canDeleteGuild =
    bucket.guild !== NO_GUILD
    && totalUploads > 0
    && visibleFights.every(f => f.uploads.every(u => u.permissions.can_delete))

  const plural = totalUploads === 1 ? '' : 's'
  const deleteLabel = `Delete the ${totalUploads} parse${plural} shown for ${bucket.guild}`

  async function handleDeleteGuild(e: React.MouseEvent) {
    e.stopPropagation()
    if (deleting) return
    if (!confirm(`${deleteLabel}? Only the parses currently listed under your filters are removed. This cannot be undone.`)) return
    setDeleting(true)
    try {
      const r = await deleteFightsChunked(visibleFights)
      onDeleted(f => r.completedFightIds.has(f.id))
      if (r.partial) {
        alert(`Deleted ${r.deleted} of ${r.requested} parse${r.requested === 1 ? '' : 's'} for ${bucket.guild}${r.error ? ` — ${r.error}` : ''}. Refreshing the list.`)
        onResync?.()
      }
    } catch (err) {
      alert(`${toErrorMessage(err)}. Refreshing the list.`)
      onResync?.()
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Card className="p-0">
      <div className="flex items-center">
        <button
          type="button"
          onClick={() => setOpen(v => !v)}
          aria-expanded={open}
          className={headerBtnCls}
        >
          <Caret open={open} />
          <h2 className="font-heading text-[0.98rem] text-gold m-0">
            {bucket.guild}
          </h2>
          <span className="text-text-muted text-[0.78rem] ml-auto">
            {bucket.totalFights} fight{bucket.totalFights === 1 ? '' : 's'}{totalUploads !== bucket.totalFights ? ` (${totalUploads} uploads)` : ''}
          </span>
        </button>
        {canDeleteGuild && (
          <TrashButton onClick={handleDeleteGuild} title={deleteLabel} disabled={deleting} />
        )}
      </div>
      {open && (
        <div className="flex flex-col gap-2 px-2 pb-2.5">
          <CategorySection
            label="Raid"
            buckets={bucket.fightsByCategory.raid}
            defaultOpen
            guild={bucket.guild}
            onDeleted={onDeleted}
          />
          <CategorySection
            label="Dungeon"
            buckets={bucket.fightsByCategory.dungeon}
            defaultOpen
            guild={bucket.guild}
            onDeleted={onDeleted}
          />
          <CategorySection
            label="Other"
            buckets={bucket.fightsByCategory.other}
            defaultOpen={false}
            guild={bucket.guild}
            onDeleted={onDeleted}
          />
        </div>
      )}
    </Card>
  )
}

function TrashButton({ onClick, title, small = false, disabled = false }: {
  onClick: (e: React.MouseEvent) => void
  title: string
  small?: boolean
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      disabled={disabled}
      className="bg-transparent border-none text-text-muted cursor-pointer leading-none opacity-55 transition-[opacity,color] duration-100 disabled:cursor-wait"
      style={{
        padding: small ? '0 4px' : '0 8px',
        fontSize: small ? '0.95rem' : '1.05rem',
      }}
      onMouseEnter={ev => {
        ev.currentTarget.style.opacity = '1'
        ev.currentTarget.style.color = 'var(--danger, #e57373)'
      }}
      onMouseLeave={ev => {
        ev.currentTarget.style.opacity = '0.55'
        ev.currentTarget.style.color = 'var(--text-muted)'
      }}
    >
      ✕
    </button>
  )
}
