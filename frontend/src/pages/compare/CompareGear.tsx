import { useMemo, useState } from 'react'
import { Badge, Card } from '../../components/ui'
import { ItemTooltip, useItemTooltip } from '../../components/ItemTooltip'
import { useClasses } from '../../useClasses'
import type { Character, EquipmentSlot } from '../characterSheet'
import { tierStyle } from '../characterSheet'
import { diffGear, nullableDelta, type GearDiffRow } from './diff'
import DeltaChip from './DeltaChip'

/** One side of a mirrored slot row: icon + tier-coloured name + adorn fill. */
function GearCell({ item, adorns, align, onShow, onHide }: {
  item: EquipmentSlot | null
  adorns: { filled: number; total: number } | null
  align: 'left' | 'right'
  onShow: (itemId: string, e: React.MouseEvent, adorns?: { color: string; bonus: number }[]) => void
  onHide: () => void
}) {
  if (!item || !item.name) {
    return <div className={`text-[0.8rem] italic text-text-muted opacity-50 ${align === 'right' ? 'text-right' : ''}`}>Empty</div>
  }
  const icon = (
    <img
      src={item.icon_id ? `/icons/${item.icon_id}.png` : '/slot-empty-blue.png'}
      alt=""
      className="w-8 h-8 shrink-0 rounded-sm border border-border"
    />
  )
  const show = (e: React.MouseEvent) => {
    if (item.item_id) {
      onShow(item.item_id, e, item.adorn_slots.filter(a => a.adorn_name != null).map(a => ({ color: a.color, bonus: a.ilvl_bonus })))
    }
  }
  return (
    <div
      className={`flex items-center gap-2 min-w-0 ${align === 'right' ? 'flex-row-reverse' : ''}`}
      onMouseOver={show}
      onMouseLeave={onHide}
      onClick={show}
    >
      {icon}
      <div className={`min-w-0 ${align === 'right' ? 'text-right' : ''}`}>
        <div className="text-[0.82rem] truncate" style={tierStyle(item.tier)}>{item.name}</div>
        {adorns && (
          <Badge variant={adorns.filled === adorns.total ? 'success' : 'muted'}>
            {adorns.filled}/{adorns.total} adorned
          </Badge>
        )}
      </div>
    </div>
  )
}

function SlotRowMirror({ row, onShow, onHide }: {
  row: GearDiffRow
  onShow: (itemId: string, e: React.MouseEvent, adorns?: { color: string; bonus: number }[]) => void
  onHide: () => void
}) {
  const differs = !row.identical && (row.a !== null || row.b !== null)
  return (
    <div className={`grid grid-cols-[1fr_84px_1fr] gap-2 items-center py-1.5 border-b border-border last:border-b-0 ${row.identical ? 'opacity-70' : ''}`}>
      <GearCell item={row.a} adorns={row.adornsA} align="left" onShow={onShow} onHide={onHide} />
      <div className="text-center">
        <div className={`text-[0.68rem] uppercase tracking-[0.08em] ${differs ? 'text-gold' : 'text-text-muted'}`}>{row.label}</div>
        {row.identical && <div className="text-text-muted opacity-60 text-[0.8rem]">=</div>}
      </div>
      <GearCell item={row.b} adorns={row.adornsB} align="right" onShow={onShow} onHide={onHide} />
    </div>
  )
}

/** Mirrored paperdoll comparison: [A item | slot | B item] rows + ilvl headline. */
export default function CompareGear({ charA, charB }: { charA: Character; charB: Character }) {
  const { tooltip, showTip, hideTip, moveTip } = useItemTooltip()
  const { colourFor } = useClasses()
  const [diffOnly, setDiffOnly] = useState(false)
  const gear = useMemo(() => diffGear(charA.equipment, charB.equipment), [charA, charB])

  const filterRows = (rows: GearDiffRow[]) =>
    rows.filter(r => (r.a !== null || r.b !== null) && (!diffOnly || !r.identical))

  const sections: [string, GearDiffRow[]][] = [
    ['Armour & Weapons', filterRows(gear.left)],
    ['Accessories', filterRows(gear.right)],
    ['Consumables', filterRows(gear.consumables)],
  ]

  return (
    <div onMouseMove={moveTip}>
      {/* Headline: ilvl + differing count — values carry names so the mirrored
          columns below are unambiguous. */}
      <Card className="rounded-sm px-4 py-2.5 mb-4 flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <span className="text-[0.85rem]">
          <span className="text-text-muted">Item Level:</span>{' '}
          <span style={{ color: colourFor(charA.cls, 'var(--text)') }}>
            {charA.name} <span className="font-semibold tabular-nums">{charA.ilvl != null ? Math.round(charA.ilvl) : '—'}</span>
          </span>
          <span className="text-text-muted"> vs </span>
          <span style={{ color: colourFor(charB.cls, 'var(--text)') }}>
            {charB.name} <span className="font-semibold tabular-nums">{charB.ilvl != null ? Math.round(charB.ilvl) : '—'}</span>
          </span>{' '}
          <DeltaChip delta={nullableDelta(charA.ilvl != null ? Math.round(charA.ilvl) : null, charB.ilvl != null ? Math.round(charB.ilvl) : null)} fmt="int" />
        </span>
        <span className="text-[0.82rem] text-text-muted">
          {gear.differingCount} of {gear.occupiedCount} slots differ
        </span>
        <label className="flex items-center gap-1.5 text-[0.72rem] text-text-muted cursor-pointer select-none ml-auto">
          <input type="checkbox" checked={diffOnly} onChange={e => setDiffOnly(e.target.checked)} />
          differences only
        </label>
      </Card>

      {/* Column identity for the mirrored rows */}
      <div className="grid grid-cols-[1fr_84px_1fr] gap-2 mb-2 text-[0.72rem] uppercase tracking-[0.08em]">
        <span className="font-semibold truncate" style={{ color: colourFor(charA.cls, 'var(--gold)') }}>{charA.name}</span>
        <span />
        <span className="font-semibold truncate text-right" style={{ color: colourFor(charB.cls, 'var(--gold)') }}>{charB.name}</span>
      </div>

      {sections.map(([title, rows]) =>
        rows.length === 0 ? null : (
          <div key={title} className="mb-4">
            <div className="text-[0.7rem] uppercase tracking-[0.12em] text-gold/70 font-heading font-semibold mb-1.5">{title}</div>
            <Card className="rounded-sm px-3 py-1">
              {rows.map(row => (
                <SlotRowMirror key={row.slotKey} row={row} onShow={showTip} onHide={hideTip} />
              ))}
            </Card>
          </div>
        )
      )}

      {tooltip && <ItemTooltip state={tooltip} />}
    </div>
  )
}
