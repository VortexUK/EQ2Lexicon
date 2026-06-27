/**
 * ZoneDaySection — one collapsible date+zone bucket under a CategorySection.
 *
 * Renders a grid header row followed by FightRow children. Defaults to
 * collapsed so a fresh load shows a compact list of zone-day buckets the
 * user can expand on demand rather than a wall of fights.
 */
import { useState } from 'react'

import Caret from '../../components/Caret'

import { FightRow } from './FightRow'
import type { ParseEncounterSummary, ZoneDayBucket } from './types'

const HDR_CELL_CLS = 'text-text-muted text-[0.7rem] uppercase tracking-[0.06em] py-1 border-b border-border mb-1'

export interface ZoneDaySectionProps {
  bucket: ZoneDayBucket
  guild: string
  onDeleted: (pred: (e: ParseEncounterSummary) => boolean) => void
}

export function ZoneDaySection({ bucket, guild, onDeleted }: ZoneDaySectionProps) {
  // Default-collapsed so a fresh load shows a compact list of zone-day
  // buckets rather than every fight at once. Same component-local state
  // pattern as CategorySection — refreshing the page resets to default.
  const [open, setOpen] = useState(false)
  // Reference `guild` so the unused-var lint passes — kept on the props
  // so a future "delete this zone-day" button has the guild context it
  // would need without needing to drill through CategorySection again.
  void guild
  return (
    <div className="flex flex-col gap-0.5">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
        aria-label={`${bucket.key} · ${bucket.fights.length} fight${bucket.fights.length === 1 ? '' : 's'}`}
        className="appearance-none border-0 bg-transparent p-0 flex items-baseline gap-2 cursor-pointer text-left"
      >
        <Caret open={open} />
        <span className="text-text text-[0.85rem]">{bucket.key}</span>
        <span className="text-text-muted text-[0.7rem] tabular-nums">
          · {bucket.fights.length}
        </span>
      </button>
      {open && (
        <div
          className="grid items-center gap-x-2 gap-y-0.5 text-[0.82rem] pl-4"
          style={{ gridTemplateColumns: '1fr 70px 70px 110px 90px 60px 130px 28px' }}
        >
          <div className={HDR_CELL_CLS}>Encounter</div>
          <div className={`${HDR_CELL_CLS} text-right`}>Time</div>
          <div className={`${HDR_CELL_CLS} text-right`}>Dur</div>
          <div className={`${HDR_CELL_CLS} text-right`}>Damage</div>
          <div className={`${HDR_CELL_CLS} text-right`}>DPS</div>
          <div className={`${HDR_CELL_CLS} text-right`}>Size</div>
          <div className={HDR_CELL_CLS}>Uploader</div>
          <div className={HDR_CELL_CLS} />
          {bucket.fights.map(f => (
            <FightRow
              key={f.id}
              fight={f}
              onDeleted={onDeleted}
            />
          ))}
        </div>
      )}
    </div>
  )
}
