import { useEffect, useMemo, useState } from 'react'
import { Badge, Card } from '../../components/ui'
import { ItemTooltip, useItemTooltip } from '../../components/ItemTooltip'
import { useClasses } from '../../useClasses'
import { useFetch } from '../../hooks/useFetch'
import type { CharGearSets, Character, EquipmentSlot, GearSet } from '../characterSheet'
import { tierStyle } from '../characterSheet'
import { diffGear, nullableDelta, type GearDiffRow } from './diff'
import DeltaChip from './DeltaChip'

/** One side's gear selection: the live equipment or a chosen saved set.
 * Fetching lives here (not ComparePage) so the sets load only when the Gear
 * tab is actually opened. */
function useGearSelection(char: Character) {
  const { data } = useFetch<CharGearSets>(`/api/character/${encodeURIComponent(char.name)}/gear-sets`)
  const [setName, setSetName] = useState<string | null>(null)
  useEffect(() => { setSetName(null) }, [char.name])
  // useFetch keeps the previous character's payload during a refetch — only
  // trust sets stamped with this character's name.
  const sets: GearSet[] = data?.character_name.toLowerCase() === char.name.toLowerCase() ? data.sets : []
  const active = setName ? sets.find(s => s.name === setName) ?? null : null
  return {
    sets,
    setName,
    setSetName,
    equipment: active ? active.equipment : char.equipment,
    ilvl: active ? active.ilvl : char.ilvl,
    label: active ? active.name : null,
  }
}

/** "Current gear" / saved-set dropdown for one column. Hidden when the
 * character has no saved sets. */
function GearSetSelect({ sel, align }: { sel: ReturnType<typeof useGearSelection>; align: 'left' | 'right' }) {
  if (sel.sets.length === 0) return null
  return (
    <select
      value={sel.setName ?? ''}
      onChange={e => sel.setSetName(e.target.value || null)}
      className={`block bg-surface border border-border rounded-sm px-2 py-0.5 mt-1 text-[0.75rem] normal-case tracking-normal font-normal max-w-full cursor-pointer ${align === 'right' ? 'ml-auto' : ''}`}
      aria-label="Gear set"
    >
      <option value="">Current gear</option>
      {sel.sets.map(s => (
        <option key={s.name} value={s.name}>{s.name}</option>
      ))}
    </select>
  )
}

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

/** Mirrored paperdoll comparison: [A item | slot | B item] rows + ilvl headline.
 * Each side can compare its live gear or any saved in-game gear set. */
export default function CompareGear({ charA, charB }: { charA: Character; charB: Character }) {
  const { tooltip, showTip, hideTip, moveTip } = useItemTooltip()
  const { colourFor } = useClasses()
  const [diffOnly, setDiffOnly] = useState(false)
  const selA = useGearSelection(charA)
  const selB = useGearSelection(charB)
  const gear = useMemo(() => diffGear(selA.equipment, selB.equipment), [selA.equipment, selB.equipment])

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
            {charA.name} <span className="font-semibold tabular-nums">{selA.ilvl != null ? Math.round(selA.ilvl) : '—'}</span>
          </span>
          <span className="text-text-muted"> vs </span>
          <span style={{ color: colourFor(charB.cls, 'var(--text)') }}>
            {charB.name} <span className="font-semibold tabular-nums">{selB.ilvl != null ? Math.round(selB.ilvl) : '—'}</span>
          </span>{' '}
          <DeltaChip delta={nullableDelta(selA.ilvl != null ? Math.round(selA.ilvl) : null, selB.ilvl != null ? Math.round(selB.ilvl) : null)} fmt="int" />
        </span>
        <span className="text-[0.82rem] text-text-muted">
          {gear.differingCount} of {gear.occupiedCount} slots differ
        </span>
        <label className="flex items-center gap-1.5 text-[0.72rem] text-text-muted cursor-pointer select-none ml-auto">
          <input type="checkbox" checked={diffOnly} onChange={e => setDiffOnly(e.target.checked)} />
          differences only
        </label>
      </Card>

      {/* Column identity for the mirrored rows + per-side gear-set choice */}
      <div className="grid grid-cols-[1fr_84px_1fr] gap-2 mb-2 text-[0.72rem] uppercase tracking-[0.08em]">
        <div className="min-w-0">
          <span className="font-semibold truncate block" style={{ color: colourFor(charA.cls, 'var(--gold)') }}>
            {charA.name}{selA.label && <span className="text-text-muted font-normal"> — {selA.label}</span>}
          </span>
          <GearSetSelect sel={selA} align="left" />
        </div>
        <span />
        <div className="min-w-0 text-right">
          <span className="font-semibold truncate block" style={{ color: colourFor(charB.cls, 'var(--gold)') }}>
            {charB.name}{selB.label && <span className="text-text-muted font-normal"> — {selB.label}</span>}
          </span>
          <GearSetSelect sel={selB} align="right" />
        </div>
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
