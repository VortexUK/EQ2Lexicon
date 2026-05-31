/**
 * Admin-only modal pickers for /raids curation:
 *
 *   - <ZonePickerModal> — pick from raid_x4/raid_x2 zones in an expansion
 *     that are NOT yet featured. Backed by GET /api/raids/zones/available.
 *
 *   - <ExpansionPickerModal> — pick from expansions in zones.db that are
 *     NOT yet featured. Backed by GET /api/raids/expansions/available.
 *
 * Both use the same lightweight overlay pattern: fixed-position dim
 * backdrop + centred Card. No external dialog library — the UI is small
 * enough that hand-rolled is simpler than introducing a dep.
 */
import { createPortal } from 'react-dom'
import { Button, Card } from '../../components/ui'
import { useFetch } from '../../hooks/useFetch'
import type { Zone } from './types'

interface AvailableExpansion {
  short: string
  name: string | null
  year: number | null
}

interface ZonePickerProps {
  expansion: string
  onPick: (zoneName: string) => void
  onClose: () => void
}

export function ZonePickerModal({ expansion, onPick, onClose }: ZonePickerProps) {
  const fetch = useFetch<Zone[]>(`/api/raids/zones/available?expansion=${encodeURIComponent(expansion)}`)
  const zones = fetch.data ?? []

  return (
    <Backdrop onClose={onClose}>
      <Card className="w-full max-w-md max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-heading text-gold text-[1.1rem]">Add raid zone — {expansion}</h2>
          <Button variant="ghost" size="icon" aria-label="Close" onClick={onClose}>×</Button>
        </div>
        {fetch.loading && <p className="text-text-muted text-sm">Loading…</p>}
        {fetch.error && <p className="text-danger text-sm">Failed to load: {fetch.error}</p>}
        {!fetch.loading && !fetch.error && zones.length === 0 && (
          <p className="text-text-muted text-sm">
            No raid-tagged zones available to add. Every raid_x4 / raid_x2
            zone in this expansion is already featured.
          </p>
        )}
        {zones.length > 0 && (
          <ul className="flex flex-col gap-1 overflow-y-auto flex-1 min-h-0" role="listbox" aria-label="Available raid zones">
            {zones.map(z => (
              <li key={z.name}>
                <button
                  type="button"
                  onClick={() => onPick(z.name)}
                  className="
                    w-full text-left px-3 py-2 rounded-md cursor-pointer
                    appearance-none border border-border bg-surface-raised
                    text-text hover:border-gold/60 hover:bg-surface
                    transition-colors
                  "
                >
                  <div className="font-heading text-gold-bright">{z.name}</div>
                  <div className="text-text-muted text-xs">
                    {z.types.filter(t => t.startsWith('raid_')).join(', ') || z.types.join(', ')}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </Backdrop>
  )
}

interface ExpansionPickerProps {
  onPick: (expansionShort: string) => void
  onClose: () => void
}

export function ExpansionPickerModal({ onPick, onClose }: ExpansionPickerProps) {
  const fetch = useFetch<AvailableExpansion[]>('/api/raids/expansions/available')
  const expansions = fetch.data ?? []

  return (
    <Backdrop onClose={onClose}>
      <Card className="w-full max-w-md max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-heading text-gold text-[1.1rem]">Add expansion</h2>
          <Button variant="ghost" size="icon" aria-label="Close" onClick={onClose}>×</Button>
        </div>
        {fetch.loading && <p className="text-text-muted text-sm">Loading…</p>}
        {fetch.error && <p className="text-danger text-sm">Failed to load: {fetch.error}</p>}
        {!fetch.loading && !fetch.error && expansions.length === 0 && (
          <p className="text-text-muted text-sm">
            Every expansion in zones.db is already featured (or implicitly
            featured via a curated raid zone).
          </p>
        )}
        {expansions.length > 0 && (
          <ul className="flex flex-col gap-1 overflow-y-auto flex-1 min-h-0" role="listbox" aria-label="Available expansions">
            {expansions.map(e => (
              <li key={e.short}>
                <button
                  type="button"
                  onClick={() => onPick(e.short)}
                  className="
                    w-full text-left px-3 py-2 rounded-md cursor-pointer
                    appearance-none border border-border bg-surface-raised
                    text-text hover:border-gold/60 hover:bg-surface
                    transition-colors
                  "
                >
                  <div className="font-heading text-gold-bright">{e.name ?? e.short}</div>
                  <div className="text-text-muted text-xs tabular-nums">
                    {e.short}{e.year ? ` · ${e.year}` : ''}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </Backdrop>
  )
}

function Backdrop({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  // Portal to document.body so the fixed-position backdrop escapes any
  // ancestor stacking context (a parent <Card>, <main>, or anything with
  // transform/filter/backdrop-filter creates a new context that would
  // trap our z-modal: 1000 inside the parent's stack — making the modal
  // render BEHIND sibling content on the page). Portals attach to body
  // which is always at z-index 0 with no transforms, so z-modal wins.
  return createPortal(
    <div
      className="fixed inset-0 z-modal flex items-center justify-center bg-bg/70 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div onClick={e => e.stopPropagation()} className="contents">
        {children}
      </div>
    </div>,
    document.body,
  )
}
