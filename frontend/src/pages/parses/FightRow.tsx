/**
 * FightRow — one row per fight inside a ZoneDaySection's grid.
 *
 * For multi-uploader fights (uploads.length > 1) the title becomes a toggle
 * that expands a nested per-uploader list; otherwise it's a direct link to
 * the parse view. TrashButton is module-local (only used here).
 */
import type { CSSProperties } from 'react'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { Badge } from '../../components/ui'
import { UploaderTag } from '../../components/UploaderTag'
import Caret from '../../components/Caret'
import { fmtDuration, fmtLocalTime, fmtNum } from '../../formatters'
import { outcomeColor } from '../../encounterOutcome'
import { isBoss } from '../ParsesPage'

import { deleteBatch, deleteOne } from './api'
import type { ParseEncounterSummary, ParseUploadSummary } from './types'

export interface FightRowProps {
  fight: ParseEncounterSummary
  onDeleted: (pred: (e: ParseEncounterSummary) => boolean) => void
}

// One row per fight inside a CategorySection's grid. For multi-uploader
// fights (uploads.length > 1) the title becomes a toggle that expands a
// nested per-uploader list; otherwise it's a direct link to the parse view.
export function FightRow({ fight, onDeleted }: FightRowProps) {
  const e = fight  // top-level fields are the canonical upload's
  const isMirror = fight.uploads.length > 1
  const [expanded, setExpanded] = useState(false)

  // "Delete the whole encounter" is only offered when the caller can delete
  // every upload in the group — i.e. an admin or an officer of the fight's
  // guild. A plain uploader can only delete their own among several, so this
  // is false for them (they still get their per-upload trash in the expansion).
  const canDeleteAll = isMirror && fight.uploads.length > 0 && fight.uploads.every(u => u.permissions.can_delete)

  // ACT outcome (encounterOutcome.ts): 1 = win (success), 2 = partial
  // (warning), 3 = loss (danger), 0 = unknown (plain text).
  const titleColor = outcomeColor(e.success_level, 'var(--text)')
  // Boss rows get a subtle yellow tint so they stand out at a glance.
  // Applied per-cell because the grid cells are direct siblings — no row
  // wrapper to style.
  const rowBg = isBoss(e.title) ? 'rgba(255, 204, 102, 0.07)' : undefined
  const cellBase: CSSProperties = { padding: '4px 0', background: rowBg }

  async function handleDeletePrimary(ev: React.MouseEvent) {
    ev.preventDefault()
    ev.stopPropagation()
    if (!confirm(`Delete encounter "${e.title}"? This cannot be undone.`)) return
    try {
      await deleteOne(e.id)
      onDeleted(other => other.id === e.id)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  async function handleDeleteFight(ev: React.MouseEvent) {
    ev.preventDefault()
    ev.stopPropagation()
    const n = fight.uploads.length
    if (!confirm(`Delete this entire encounter — all ${n} uploads of "${e.title}"? This cannot be undone.`)) return
    try {
      await deleteBatch(fight.uploads.map(u => u.id))
      onDeleted(other => other.id === e.id)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  async function handleDeleteUpload(upload: ParseUploadSummary, ev: React.MouseEvent) {
    ev.preventDefault()
    ev.stopPropagation()
    if (!confirm(`Delete ${upload.uploaded_by}'s upload of "${e.title}"? This cannot be undone.`)) return
    try {
      await deleteOne(upload.id)
      onDeleted(other => other.id === upload.id)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  // For a non-mirror row, the title links straight to the parse and the
  // trash deletes that single encounter — same UX as before. For a mirror
  // group, the title click toggles expansion (no direct /parse navigation
  // since there are multiple options); the top-level trash deletes the whole
  // encounter (all uploads) and is shown only to those allowed to remove
  // every upload — admins and officers of the fight's guild.
  return (
    <>
      {isMirror ? (
        <button
          onClick={() => setExpanded(v => !v)}
          className="flex items-center gap-1.5 border-none text-left cursor-pointer py-1 no-underline"
          style={{
            ...cellBase, color: titleColor,
            background: rowBg ?? 'none', font: 'inherit',
          }}
        >
          <Caret open={expanded} />
          {e.title}
          <span className="text-[0.7rem] text-text-muted font-normal">
            {fight.uploads.length} uploads
          </span>
        </button>
      ) : (
        <Link
          to={`/parse/${e.id}`}
          className="no-underline"
          style={{ ...cellBase, color: titleColor }}
        >
          {e.title}
        </Link>
      )}
      <div className="text-right text-text-muted" style={cellBase}>{fmtLocalTime(e.started_at)}</div>
      <div className="text-right text-text-muted" style={cellBase}>{fmtDuration(e.duration_s)}</div>
      <div className="text-right" style={cellBase}>{fmtNum(e.total_damage)}</div>
      <div className="text-right text-gold" style={cellBase}>{fmtNum(Math.round(e.encdps))}</div>
      <div className="text-right" style={cellBase}>
        <Badge variant="muted">{e.player_count}p</Badge>
      </div>
      <div className="text-text-muted truncate" style={cellBase}>
        <UploaderTag
          characterName={e.uploaded_by}
          discordId={e.uploader_discord_id}
          displayName={e.uploader_display_name}
        />
        {isMirror && (
          <span className="ml-1 text-[0.7rem] text-text-muted">
            +{fight.uploads.length - 1}
          </span>
        )}
      </div>
      <div className="text-center" style={cellBase}>
        {!isMirror && e.permissions.can_delete && (
          <TrashButton onClick={handleDeletePrimary} title="Delete this encounter" small />
        )}
        {/* Mirror group: officers/admins (who can delete every upload) get a
            single button that removes the whole encounter at once. */}
        {canDeleteAll && (
          <TrashButton
            onClick={handleDeleteFight}
            title={`Delete entire encounter (all ${fight.uploads.length} uploads)`}
            small
          />
        )}
      </div>

      {isMirror && expanded && (
        <div
          className="col-[1/-1] flex flex-col gap-0.5 text-[0.78rem] pt-1 pb-2 pl-6"
          style={{ background: rowBg ?? undefined }}
        >
          <div className="text-text-muted text-[0.7rem] mb-0.5">
            Pick a raider's view:
          </div>
          {fight.uploads.map(u => (
            <div
              key={u.id}
              className="grid items-center gap-x-2"
              style={{ gridTemplateColumns: '1fr 70px 110px 90px 28px' }}
            >
              <Link
                to={`/parse/${u.id}`}
                className="text-text no-underline"
              >
                <span className="text-gold">
                  <UploaderTag
                    characterName={u.uploaded_by}
                    discordId={u.uploader_discord_id}
                    displayName={u.uploader_display_name}
                  />
                </span>
                {u.id === fight.id && (
                  <span className="ml-[0.4rem] text-[0.65rem] text-text-muted">(primary)</span>
                )}
              </Link>
              <span className="text-right text-text-muted">{fmtDuration(u.duration_s)}</span>
              <span className="text-right">{fmtNum(u.total_damage)}</span>
              <span className="text-right text-gold">{fmtNum(Math.round(u.encdps))}</span>
              <span className="text-center">
                {u.permissions.can_delete && (
                  <TrashButton onClick={ev => handleDeleteUpload(u, ev)} title={`Delete ${u.uploaded_by}'s upload`} small />
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

function TrashButton({ onClick, title, small = false }: {
  onClick: (e: React.MouseEvent) => void
  title: string
  small?: boolean
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="bg-transparent border-none text-text-muted cursor-pointer leading-none opacity-55 transition-[opacity,color] duration-100"
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
