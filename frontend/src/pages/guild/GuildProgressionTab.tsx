/**
 * GuildProgressionTab — the RoK progression matrix for a guild.
 *
 * One row per census-known member (level 65+), columns for Epic / Mythical /
 * T1–T4 tier flags / Trakanon access. Cells show ✓ or a fraction; mousing
 * over shows the detail (missing bosses for a partial tier, "Step 5/11 —
 * Quest Name" for a partial epic) via the same fixed-position tooltip
 * pattern as the spell/adorn check tabs.
 *
 * Data: GET /api/guild/{name}/progression (server-side census reduce,
 * SWR-cached ~15 min).
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useFetch } from '../../hooks/useFetch'
import { chainStepLabel, type ProgressionData } from '../CharacterProgressionTab'
import { TD_CLS, TH_CLS } from './types'

const TABLE_CLS = 'w-full border-collapse text-left'

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

/** Cell payload: display text + colour class + hover detail. */
interface Cell {
  text: string
  cls: string
  title: string
  lines: { text: string; ok?: boolean }[]
}

function epicCell(p: ProgressionData, slot: 'fabled' | 'mythical'): Cell {
  const epic = p.epic
  if (!epic) return { text: '—', cls: 'text-text-muted', title: 'No epic data for this class', lines: [] }
  const chain = epic[slot]
  const title = `${slot === 'fabled' ? 'Fabled' : 'Mythical'} — ${epic.weapon}`
  if (chain.done) {
    return { text: '✓', cls: 'text-success font-semibold', title, lines: [{ text: `Completed${chain.date ? ` · ${chain.date}` : ''}`, ok: true }] }
  }
  const step = chainStepLabel(chain)
  if (step) {
    const lines = [{ text: step }]
    if (chain.current_stage) lines.push({ text: chain.current_stage })
    return { text: `${chain.steps_done}/${chain.steps_total}`, cls: 'text-warning', title, lines }
  }
  return { text: '—', cls: 'text-text-muted', title, lines: [{ text: 'Not started' }] }
}

function tierCell(p: ProgressionData, tier: string): Cell {
  const t = p.tiers?.[tier]
  if (!t) return { text: '—', cls: 'text-text-muted', title: tier, lines: [] }
  const lines = t.bosses.map(b => ({ text: `${b.earned ? '✓' : '✗'} ${b.boss}`, ok: b.earned }))
  if (t.complete) return { text: '✓', cls: 'text-success font-semibold', title: `${tier} — complete`, lines }
  if (t.earned === 0) return { text: '—', cls: 'text-text-muted', title: `${tier} — none`, lines }
  return { text: `${t.earned}/${t.total}`, cls: 'text-warning', title: `${tier} — ${t.earned}/${t.total}`, lines }
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
    }
  }
  if (t.state === 'ready_to_turn_in') {
    return { text: 'RDY', cls: 'text-warning font-semibold', title: 'Trakanon access', lines: [{ text: 'All bosses dead — turn-in pending (Snyr’dok)' }, ...killLine] }
  }
  if (t.state === 'in_progress') {
    const lines = (t.bosses ?? []).map(b => ({ text: `${b.killed ? '✓' : '✗'} ${b.boss}`, ok: b.killed }))
    return { text: `${t.killed ?? 0}/${t.total ?? 12}`, cls: 'text-warning', title: 'Taking on Trakanon', lines: [...lines, ...killLine] }
  }
  return { text: '—', cls: 'text-text-muted', title: 'Trakanon access', lines: [{ text: 'Quest not started' }, ...killLine] }
}

export function GuildProgressionTab({ guildName }: { guildName: string }) {
  const { data, loading, error } = useFetch<GuildProgressionResponse>(
    `/api/guild/${encodeURIComponent(guildName)}/progression`,
  )
  const [tip, setTip] = useState<HoverTip | null>(null)

  function showTip(e: React.MouseEvent<HTMLTableCellElement>, cell: Cell) {
    if (cell.lines.length === 0) return
    const r = e.currentTarget.getBoundingClientRect()
    setTip({ x: Math.min(r.left, window.innerWidth - 280), y: r.bottom + 6, title: cell.title, lines: cell.lines })
  }

  if (loading) return <p className="text-text-muted py-6">Building progression matrix… (first load fetches the whole roster from Census)</p>
  if (error || !data) return <p className="text-text-muted py-6">Progression unavailable (Census may be down) — try again shortly.</p>
  if (data.members.length === 0) return <p className="text-text-muted py-6">No census-known members at level 65+.</p>

  return (
    <div>
      <p className="text-text-muted text-[0.8rem] mb-3">
        Level 65+ members Census knows about (recently-logged-in characters only). Hover a cell for detail.
      </p>
      <div className="overflow-x-auto border border-border rounded-md">
        <table className={TABLE_CLS}>
          <thead>
            <tr className="bg-white/2">
              <th className={TH_CLS}>Member</th>
              <th className={TH_CLS}>Lvl</th>
              <th className={TH_CLS}>Class</th>
              <th className={`${TH_CLS} text-center`}>Epic</th>
              <th className={`${TH_CLS} text-center`}>Mythical</th>
              {TIERS.map(t => <th key={t} className={`${TH_CLS} text-center`}>{t}</th>)}
              <th className={`${TH_CLS} text-center`} title="Taking on Trakanon access quest">Trak</th>
            </tr>
          </thead>
          <tbody>
            {data.members.map(m => {
              const cells: Cell[] = [
                epicCell(m.progression, 'fabled'),
                epicCell(m.progression, 'mythical'),
                ...TIERS.map(t => tierCell(m.progression, t)),
                trakCell(m.progression),
              ]
              return (
                <tr key={m.name}>
                  <td className={TD_CLS}>
                    <Link to={`/character/${encodeURIComponent(m.name)}`} className="text-gold no-underline hover:underline">
                      {m.name}
                    </Link>
                  </td>
                  <td className={`${TD_CLS} text-text-muted`}>{m.level ?? '—'}</td>
                  <td className={`${TD_CLS} text-text-muted`}>{m.cls ?? '—'}</td>
                  {cells.map((c, i) => (
                    <td
                      key={i}
                      className={`${TD_CLS} text-center cursor-default ${c.cls}`}
                      onMouseEnter={e => showTip(e, c)}
                      onMouseLeave={() => setTip(null)}
                      onClick={e => showTip(e, c)}
                    >
                      {c.text}
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

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
