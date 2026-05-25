import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import Breadcrumb from '../components/Breadcrumb'
import { Card } from '../components/ui'
import { fmtDuration, fmtLocalDate, fmtNum } from '../formatters'
import { percentileColor } from '../percentileColors'

interface FilterZone { zone: string; bosses: string[] }
interface FilterScope { key: string; label: string; zones: FilterZone[] }
interface FiltersResponse { scopes: FilterScope[] }

interface RankingRow {
  kind: 'character' | 'guild'
  encounter_id: number
  percentile: number
  size: number
  started_at: number
  name: string | null
  guild_name: string | null
  level: number | null
  cls: string | null
  score: number | null
  duration_s: number | null
}
interface RankingsResponse { rows: RankingRow[]; classes: string[]; total: number }

const METRICS = [
  { key: 'dps', label: 'Damage (DPS)' },
  { key: 'hps', label: 'Healing (HPS)' },
  { key: 'speed', label: 'Speed' },
]

const CTRL = 'bg-surface border border-border rounded-md text-text px-2 py-1 text-sm'

export default function RankingsPage() {
  const [filters, setFilters] = useState<FiltersResponse>({ scopes: [] })
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const [board, setBoard] = useState<RankingsResponse | null>(null)
  const [loading, setLoading] = useState(false)

  const size = params.get('size') || ''
  const zone = params.get('zone') || ''
  const boss = params.get('boss') || ''
  const metric = params.get('metric') || 'dps'
  const cls = params.get('class') || ''

  function update(patch: Record<string, string>) {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v); else next.delete(k)
    }
    setParams(next)
  }

  useEffect(() => {
    fetch('/api/rankings/filters', { credentials: 'include' })
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((j: FiltersResponse) => setFilters(j))
      .catch(() => setFilters({ scopes: [] }))
  }, [])

  const scope = useMemo(() => filters.scopes.find(s => s.key === size), [filters, size])
  const zoneObj = useMemo(() => scope?.zones.find(z => z.zone === zone), [scope, zone])

  useEffect(() => {
    if (!size || !zone || !boss) { setBoard(null); return }
    const u = new URL('/api/rankings', window.location.origin)
    u.searchParams.set('size', size); u.searchParams.set('zone', zone)
    u.searchParams.set('boss', boss); u.searchParams.set('metric', metric)
    if (cls && metric !== 'speed') u.searchParams.set('class', cls)
    let cancelled = false
    setLoading(true)
    fetch(u.toString(), { credentials: 'include' })
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((j: RankingsResponse) => { if (!cancelled) setBoard(j) })
      .catch(() => { if (!cancelled) setBoard(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [size, zone, boss, metric, cls])

  const isSpeed = metric === 'speed'

  return (
    <main className="page-enter mx-auto max-w-5xl px-4 py-6">
      <Breadcrumb items={[{ label: 'Rankings' }]} />
      <h1 className="font-heading text-[1.7rem] text-gold mb-3">EQ2Logs — Rankings</h1>

      <div className="flex flex-wrap gap-2 mb-4">
        <select className={CTRL} value={size} onChange={e => update({ size: e.target.value, zone: '', boss: '' })}>
          <option value="">Scope…</option>
          {filters.scopes.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
        <select className={CTRL} value={zone} disabled={!scope} onChange={e => update({ zone: e.target.value, boss: '' })}>
          <option value="">Zone…</option>
          {scope?.zones.map(z => <option key={z.zone} value={z.zone}>{z.zone}</option>)}
        </select>
        <select className={CTRL} value={boss} disabled={!zoneObj} onChange={e => update({ boss: e.target.value })}>
          <option value="">Boss…</option>
          {zoneObj?.bosses.map(b => <option key={b} value={b}>{b}</option>)}
        </select>
        <select className={CTRL} value={metric} onChange={e => update({ metric: e.target.value })}>
          {METRICS.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
        </select>
        {!isSpeed && (
          <select className={CTRL} value={cls} onChange={e => update({ class: e.target.value })}>
            <option value="">All classes</option>
            {(board?.classes ?? []).map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        )}
      </div>

      {loading && <p className="text-text-muted">Loading…</p>}
      {!loading && (!size || !zone || !boss) && (
        <p className="text-text-muted">Pick a scope, zone, and boss to see the rankings.</p>
      )}
      {!loading && board && size && zone && boss && (
        <Card className="p-0 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-text-muted text-left text-[0.72rem] uppercase tracking-wide border-b border-border">
                <th className="px-3 py-2">#</th>
                <th className="px-3 py-2">%</th>
                <th className="px-3 py-2">{isSpeed ? 'Guild' : 'Player'}</th>
                {!isSpeed && <th className="px-3 py-2">Lvl</th>}
                {!isSpeed && <th className="px-3 py-2">Class</th>}
                <th className="px-3 py-2 text-right">{isSpeed ? 'Time' : metric === 'hps' ? 'HPS' : 'DPS'}</th>
                <th className="px-3 py-2 text-right">Size</th>
                <th className="px-3 py-2 text-right">Date</th>
              </tr>
            </thead>
            <tbody>
              {board.rows.map((r, i) => (
                <tr
                  key={`${r.encounter_id}-${r.name ?? r.guild_name}`}
                  className="border-b border-border/40 hover:bg-surface/60 cursor-pointer"
                  onClick={() => navigate(`/parse/${r.encounter_id}`)}
                >
                  <td className="px-3 py-2">{i + 1}</td>
                  <td className="px-3 py-2 font-bold" style={{ color: percentileColor(r.percentile) }}>{r.percentile}</td>
                  <td className="px-3 py-2">
                    {isSpeed ? (
                      <Link to={`/parse/${r.encounter_id}`} className="text-text underline decoration-dotted underline-offset-2" onClick={e => e.stopPropagation()}>
                        {r.guild_name}
                      </Link>
                    ) : (
                      <>
                        <Link to={`/parse/${r.encounter_id}`} className="text-text underline decoration-dotted underline-offset-2" onClick={e => e.stopPropagation()}>
                          {r.name}
                        </Link>
                        {r.guild_name && <span className="text-text-muted text-[0.7rem] ml-1">‹{r.guild_name}›</span>}
                      </>
                    )}
                  </td>
                  {!isSpeed && <td className="px-3 py-2 tabular-nums">{r.level ?? '—'}</td>}
                  {!isSpeed && <td className="px-3 py-2">{r.cls ?? '—'}</td>}
                  <td className="px-3 py-2 text-right tabular-nums">
                    {isSpeed ? fmtDuration(r.duration_s ?? 0) : fmtNum(Math.round(r.score ?? 0))}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{r.size}</td>
                  <td className="px-3 py-2 text-right text-text-muted">{fmtLocalDate(r.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {board.rows.length === 0 && <p className="text-text-muted p-3">No ranked kills yet for this board.</p>}
        </Card>
      )}
    </main>
  )
}
