import { useMemo, useState } from 'react'
import { Card, SectionLabel } from '../../components/ui'
import { useClasses } from '../../useClasses'
import type { Character } from '../characterSheet'
import { fmtStat } from '../characterSheet'
import { diffStats } from './diff'
import DeltaChip from './DeltaChip'

function Cell({ value, fmt }: { value: number | null; fmt?: Parameters<typeof fmtStat>[1] }) {
  if (value === null) return <span className="text-text-muted opacity-50 text-right">—</span>
  return <span className="text-[0.85rem] font-medium text-right tabular-nums">{fmtStat(value, fmt)}</span>
}

/** Grouped stat comparison: label | A | B | Δ per row, groups per STAT_GROUPS. */
export default function CompareStats({ charA, charB }: { charA: Character; charB: Character }) {
  const { colourFor } = useClasses()
  const [diffOnly, setDiffOnly] = useState(false)
  const groups = useMemo(() => diffStats(charA.stats, charB.stats), [charA, charB])

  // A null delta (stat present on one side only) IS a difference — keep it.
  const shown = diffOnly
    ? groups
        .map(g => ({ ...g, rows: g.rows.filter(r => r.delta !== 0) }))
        .filter(g => g.rows.length > 0)
    : groups

  return (
    <div>
      {/* Column header: names stay visible while scrolling the groups */}
      <div className="sticky top-14 z-10 bg-bg/90 backdrop-blur-sm border-b border-border mb-3 py-1.5 grid grid-cols-[1fr_auto_auto_auto] gap-x-4 items-baseline">
        <label className="flex items-center gap-1.5 text-[0.72rem] text-text-muted cursor-pointer select-none">
          <input type="checkbox" checked={diffOnly} onChange={e => setDiffOnly(e.target.checked)} />
          differences only
        </label>
        <span className="text-[0.8rem] font-semibold w-[90px] text-right truncate" style={{ color: colourFor(charA.cls, 'var(--gold)') }}>
          {charA.name}
        </span>
        <span className="text-[0.8rem] font-semibold w-[90px] text-right truncate" style={{ color: colourFor(charB.cls, 'var(--gold)') }}>
          {charB.name}
        </span>
        <span className="text-[0.8rem] font-semibold w-[80px] text-right text-text-muted">Δ</span>
      </div>

      {shown.length === 0 && (
        <p className="text-text-muted text-[0.85rem]">No stat differences — identical on every reported stat.</p>
      )}

      {shown.map(group => (
        <div key={group.title} className="mb-4">
          <SectionLabel>{group.title}</SectionLabel>
          <Card className="rounded-sm px-3 py-1">
            {group.rows.map(row => (
              <div
                key={row.label}
                className="grid grid-cols-[1fr_auto_auto_auto] gap-x-4 items-baseline py-[3px] border-b border-border last:border-b-0"
              >
                <span className="text-text-muted text-[0.78rem] pr-2">{row.label}</span>
                <span className="w-[90px] text-right"><Cell value={row.a} fmt={row.fmt} /></span>
                <span className="w-[90px] text-right"><Cell value={row.b} fmt={row.fmt} /></span>
                <span className="w-[80px] text-right text-[0.82rem]"><DeltaChip delta={row.delta} fmt={row.fmt} /></span>
              </div>
            ))}
          </Card>
        </div>
      ))}
    </div>
  )
}
