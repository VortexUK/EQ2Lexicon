/**
 * GuildProgressionTab — the RoK progression matrix for a guild.
 *
 * One row per census-known member (level 65+), columns for Epic / Mythical /
 * T1–T4 tier flags / Trakanon access. Cells show ✓ or a fraction; mousing
 * over shows the detail (missing bosses for a partial tier, "Step 5/11 —
 * Quest Name" for a partial epic) via the same fixed-position tooltip
 * pattern as the spell/adorn check tabs.
 *
 * Same member treatment as the other guild tables: the shared name/class
 * filter box, sortable columns (progression columns sort most-progressed
 * first), class-coloured "Class (Level)", and the ★ own-character marker.
 *
 * Data: GET /api/guild/{name}/progression (server-side census reduce,
 * SWR-cached ~15 min).
 */
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { SortTh } from '../../components/ui/SortTh'
import { useFetch } from '../../hooks/useFetch'
import { useSortable } from '../../hooks/useSortable'
import { useClasses } from '../../useClasses'
import { chainStepLabel, type ProgressionData } from '../CharacterProgressionTab'
import { TD_CLS, TH_CLS } from './types'

interface MemberRow {
  name: string
  level: number | null
  cls: string | null
  progression: ProgressionData
}

interface GuildProgressionResponse {
  guild: string
  world: string
  members: MemberRow[]
}

const TIERS = ['T1', 'T2', 'T3', 'T4'] as const

interface HoverTip {
  x: number
  y: number
  title: string
  lines: { text: string; ok?: boolean }[]
}

/** Cell payload: display text + colour class + hover detail + sort rank
 * (bigger = further progressed). */
interface Cell {
  text: string
  cls: string
  title: string
  lines: { text: string; ok?: boolean }[]
  sortVal: number
}

function epicCell(p: ProgressionData, slot: 'fabled' | 'mythical'): Cell {
  const epic = p.epic
  if (!epic) return { text: '—', cls: 'text-text-muted', title: 'No epic data for this class', lines: [], sortVal: -1 }
  const chain = epic[slot]
  const title = `${slot === 'fabled' ? 'Fabled' : 'Mythical'} — ${epic.weapon}`
  if (chain.done) {
    return {
      text: '✓',
      cls: 'text-success font-semibold',
      title,
      lines: [{ text: `Completed${chain.date ? ` · ${chain.date}` : ''}`, ok: true }],
      sortVal: 2,
    }
  }
  const step = chainStepLabel(chain)
  if (step) {
    const lines = [{ text: step }]
    if (chain.current_stage) lines.push({ text: chain.current_stage })
    return {
      text: `${chain.steps_done}/${chain.steps_total}`,
      cls: 'text-warning',
      title,
      lines,
      sortVal: chain.steps_total > 0 ? chain.steps_done / chain.steps_total : 0,
    }
  }
  return { text: '—', cls: 'text-text-muted', title, lines: [{ text: 'Not started' }], sortVal: 0 }
}

function tierCell(p: ProgressionData, tier: string): Cell {
  const t = p.tiers?.[tier]
  if (!t) return { text: '—', cls: 'text-text-muted', title: tier, lines: [], sortVal: -1 }
  const lines = t.bosses.map(b => ({ text: `${b.earned ? '✓' : '✗'} ${b.boss}`, ok: b.earned }))
  const frac = t.total > 0 ? t.earned / t.total : 0
  if (t.complete) return { text: '✓', cls: 'text-success font-semibold', title: `${tier} — complete`, lines, sortVal: 1 }
  if (t.earned === 0) return { text: '—', cls: 'text-text-muted', title: `${tier} — none`, lines, sortVal: 0 }
  return { text: `${t.earned}/${t.total}`, cls: 'text-warning', title: `${tier} — ${t.earned}/${t.total}`, lines, sortVal: frac }
}

function trakCell(p: ProgressionData): Cell {
  const t = p.trakanon
  const killLine = t.killed_trakanon ? [{ text: '✓ Trakanon slain', ok: true }] : []
  if (t.state === 'completed') {
    return {
      text: t.killed_trakanon ? '✓★' : '✓',
      cls: 'text-success font-semibold',
      title: 'Trakanon access',
      lines: [{ text: `Access granted${t.date ? ` · ${t.date}` : ''}`, ok: true }, ...killLine],
      sortVal: t.killed_trakanon ? 2.1 : 2,
    }
  }
  if (t.state === 'ready_to_turn_in') {
    return {
      text: 'RDY',
      cls: 'text-warning font-semibold',
      title: 'Trakanon access',
      lines: [{ text: 'All bosses dead — turn-in pending (Snyr’dok)' }, ...killLine],
      sortVal: 1.5,
    }
  }
  if (t.state === 'in_progress') {
    const lines = (t.bosses ?? []).map(b => ({ text: `${b.killed ? '✓' : '✗'} ${b.boss}`, ok: b.killed }))
    return {
      text: `${t.killed ?? 0}/${t.total ?? 12}`,
      cls: 'text-warning',
      title: 'Taking on Trakanon',
      lines: [...lines, ...killLine],
      sortVal: (t.killed ?? 0) / (t.total || 12),
    }
  }
  return { text: '—', cls: 'text-text-muted', title: 'Trakanon access', lines: [{ text: 'Quest not started' }, ...killLine], sortVal: 0 }
}

// ── Sorting ───────────────────────────────────────────────────────────────────

type SortKey = 'name' | 'level' | 'epic' | 'myth' | 'T1' | 'T2' | 'T3' | 'T4' | 'trak'

const COLS: { label: string; key: SortKey; center?: boolean; title?: string }[] = [
  { label: 'Member', key: 'name' },
  { label: 'Class (Level)', key: 'level' },
  { label: 'Epic', key: 'epic', center: true },
  { label: 'Mythical', key: 'myth', center: true },
  ...TIERS.map(t => ({ label: t, key: t as SortKey, center: true })),
  { label: 'Trak', key: 'trak', center: true, title: 'Taking on Trakanon access quest' },
]

interface Row {
  name: string
  level: number | null
  cls: string | null
  cells: Record<Exclude<SortKey, 'name' | 'level'>, Cell>
}

function sortValue(r: Row, key: SortKey): string | number {
  if (key === 'name') return r.name.toLowerCase()
  if (key === 'level') return r.level ?? -1
  return r.cells[key].sortVal
}

// ── Component ─────────────────────────────────────────────────────────────────

export function GuildProgressionTab({
  guildName,
  filter,
  myChars,
}: {
  guildName: string
  filter: string
  myChars: Set<string>
}) {
  const { data, loading, error } = useFetch<GuildProgressionResponse>(
    `/api/guild/${encodeURIComponent(guildName)}/progression`,
  )
  const { colourFor } = useClasses()
  const [tip, setTip] = useState<HoverTip | null>(null)

  function showTip(e: React.MouseEvent<HTMLTableCellElement>, cell: Cell) {
    if (cell.lines.length === 0) return
    const r = e.currentTarget.getBoundingClientRect()
    setTip({ x: Math.min(r.left, window.innerWidth - 280), y: r.bottom + 6, title: cell.title, lines: cell.lines })
  }

  const rows = useMemo<Row[]>(() => {
    const q = filter.trim().toLowerCase()
    return (data?.members ?? [])
      .filter(m => !q || m.name.toLowerCase().includes(q) || (m.cls ?? '').toLowerCase().includes(q))
      .map(m => ({
        name: m.name,
        level: m.level,
        cls: m.cls,
        cells: {
          epic: epicCell(m.progression, 'fabled'),
          myth: epicCell(m.progression, 'mythical'),
          T1: tierCell(m.progression, 'T1'),
          T2: tierCell(m.progression, 'T2'),
          T3: tierCell(m.progression, 'T3'),
          T4: tierCell(m.progression, 'T4'),
          trak: trakCell(m.progression),
        },
      }))
  }, [data, filter])

  const { sorted, sortKey, sortDir, handleSort } = useSortable<Row, SortKey>(
    rows,
    sortValue,
    'name',
    'asc',
    k => (k === 'name' ? 'asc' : 'desc'),
  )

  if (loading) return <p className="text-text-muted p-4">Building progression matrix… (first load fetches the whole roster from Census)</p>
  if (error || !data) return <p className="text-text-muted p-4">Progression unavailable (Census may be down) — try again shortly.</p>
  if (data.members.length === 0) return <p className="text-text-muted p-4">No census-known members at level 65+.</p>

  return (
    <div>
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b-2 border-border bg-surface-raised">
            {COLS.map(col => (
              <SortTh
                key={col.key}
                sortKey={col.key}
                active={sortKey}
                dir={sortDir}
                onSort={handleSort}
                className={[TH_CLS, col.center ? 'text-center' : 'text-left'].join(' ')}
                title={col.title}
              >
                {col.label}
              </SortTh>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr><td colSpan={COLS.length} className={`${TD_CLS} text-center text-text-muted`}>No members match your filter.</td></tr>
          ) : sorted.map(r => {
            const clsLabel = r.cls
              ? r.level != null ? `${r.cls} (${r.level})` : r.cls
              : '—'
            const mine = myChars.has(r.name.toLowerCase())
            return (
              <tr key={r.name} className="border-b border-border" style={{ background: mine ? 'rgba(var(--gold-rgb), 0.06)' : undefined }}>
                <td className={TD_CLS}>
                  <Link to={`/character/${encodeURIComponent(r.name)}`} className="text-gold no-underline font-medium">
                    {r.name}
                  </Link>
                  {mine && <span className="ml-[0.4rem] text-[0.65rem] text-gold align-middle">★</span>}
                </td>
                <td className={TD_CLS} style={{ color: r.cls ? colourFor(r.cls, 'var(--text)') : 'var(--text-muted)' }}>
                  {clsLabel}
                </td>
                {(Object.keys(r.cells) as (keyof Row['cells'])[]).map(k => {
                  const c = r.cells[k]
                  return (
                    <td
                      key={k}
                      className={`${TD_CLS} text-center cursor-default ${c.cls}`}
                      onMouseEnter={e => showTip(e, c)}
                      onMouseLeave={() => setTip(null)}
                      onClick={e => showTip(e, c)}
                    >
                      {c.text}
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="text-text-muted text-[0.75rem] px-3 py-2">
        Level 65+ members Census knows about (recently-logged-in characters only). Hover a cell for detail;
        click a column to sort — progression columns sort most-progressed first.
      </p>

      {/* Fixed-position hover detail — escapes the scrollable table container
          (same pattern as the spell-check tab). */}
      {tip && (
        <div
          className="fixed z-tooltip bg-surface-raised border border-border rounded-md px-3 py-2 max-w-[280px] pointer-events-none"
          style={{ left: tip.x, top: tip.y, boxShadow: '0 8px 24px rgba(0,0,0,0.5)' }}
        >
          <div className="text-gold text-[0.78rem] font-semibold mb-1">{tip.title}</div>
          {tip.lines.map((l, i) => (
            <div key={i} className={`text-[0.78rem] leading-snug ${l.ok === true ? 'text-success' : l.ok === false ? 'text-text-muted' : 'text-text'}`}>
              {l.text}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
