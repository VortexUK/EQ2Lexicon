import { useEffect, useMemo, useState } from 'react'
import { useFetch } from '../hooks/useFetch'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { Card } from '../components/ui'
import { FilterBar, FilterDropdown, type DropdownOption } from '../components/FilterDropdown'
import { fmtDuration, fmtLocalDate, fmtNum } from '../formatters'
import { percentileColor } from '../percentileColors'

interface FilterZone { zone: string; bosses: string[]; expansion?: string | null }
interface FilterScope { key: string; label: string; zones: FilterZone[] }
interface RaidExpansion { short: string; name: string }
interface FiltersResponse { scopes: FilterScope[]; raid_expansions?: RaidExpansion[]; default_expansion?: string | null }

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
  ilvl: number | null
}
interface RankingsResponse { rows: RankingRow[]; classes: string[]; total: number }

// Metric options grouped Character vs Guild (à la Warcraft Logs).
const METRICS: DropdownOption[] = [
  { value: 'dps', label: 'Damage (DPS)', group: 'Character' },
  { value: 'hps', label: 'Healing (HPS)', group: 'Character' },
  { value: 'speed', label: 'Speed', group: 'Guild' },
]

export default function RankingsPage() {
  const [filters, setFilters] = useState<FiltersResponse>({ scopes: [] })
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()

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

  const scope = filters.scopes.find(s => s.key === size)
  const zoneObj = scope?.zones.find(z => z.zone === zone)
  const raidZones = filters.scopes.find(s => s.key === 'raid')?.zones ?? []
  const groupZones = filters.scopes.find(s => s.key === 'group')?.zones ?? []
  const raidExpansions = filters.raid_expansions ?? []

  // Active expansion: explicit ?xpac, else the selected zone's own expansion
  // (raids OR dungeons — both carry expansion now since dungeons came from
  // the curated zones.db tagging in #36, not from kill data), else the
  // server's default (SERVER_CURRENT_XPAC / most recent).
  const xpac = params.get('xpac') || zoneObj?.expansion || filters.default_expansion || ''
  const raidZonesForXpac = useMemo(
    () => raidZones.filter(z => (z.expansion ?? 'Other') === xpac),
    [raidZones, xpac],
  )
  // Same xpac-filter pattern for dungeons. groupZones now carry expansion
  // (server-side from the zone_types dungeon overlay) — pre-curation they
  // were kill-data-driven with expansion=null, which is why the old code
  // showed them all regardless of expansion.
  const groupZonesForXpac = useMemo(
    () => groupZones.filter(z => (z.expansion ?? 'Other') === xpac),
    [groupZones, xpac],
  )

  // Picking a zone from the top bar also selects its first boss.
  function pickZone(scopeKey: string, zoneName: string, zones: FilterZone[]) {
    const z = zones.find(x => x.zone === zoneName)
    update({ size: scopeKey, zone: zoneName, boss: z?.bosses[0] ?? '' })
  }

  // Default to the first boss when a zone is set without a valid boss (e.g. URL load).
  useEffect(() => {
    if (size && zone && zoneObj && zoneObj.bosses.length && (!boss || !zoneObj.bosses.includes(boss))) {
      update({ boss: zoneObj.bosses[0] })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size, zone, zoneObj, boss])

  const boardUrl = useMemo(() => {
    if (!size || !zone || !boss) return null
    const u = new URL('/api/rankings', window.location.origin)
    u.searchParams.set('size', size)
    u.searchParams.set('zone', zone)
    u.searchParams.set('boss', boss)
    u.searchParams.set('metric', metric)
    if (cls && metric !== 'speed') u.searchParams.set('class', cls)
    return u.toString()
  }, [size, zone, boss, metric, cls])

  const { data: board, loading } = useFetch<RankingsResponse>(boardUrl)

  const isSpeed = metric === 'speed'

  return (
    <main className="page-enter mx-auto max-w-5xl px-4 py-6">
      {/* Top — category nav: Raids · Expansion · Dungeons · Raid Guides */}
      <div className="mb-5">
        <FilterBar>
          {raidExpansions.length > 0 && (
            <FilterDropdown
              value={xpac}
              options={raidExpansions.map(e => ({ value: e.short, label: e.name }))}
              onChange={v => update({ xpac: v })}
            />
          )}
          <FilterDropdown
            label="Raids"
            active={size === 'raid'}
            value={size === 'raid' ? zone : ''}
            options={raidZonesForXpac.map(z => ({ value: z.zone, label: z.zone }))}
            onChange={v => pickZone('raid', v, raidZonesForXpac)}
          />
          <FilterDropdown
            label="Dungeons"
            active={size === 'group'}
            value={size === 'group' ? zone : ''}
            options={groupZonesForXpac.map(z => ({ value: z.zone, label: z.zone }))}
            onChange={v => pickZone('group', v, groupZonesForXpac)}
          />
          <FilterDropdown label="Raid Guides" disabled value="" options={[]} onChange={() => {}} />
        </FilterBar>
      </div>

      {/* Middle — selected zone title + type */}
      {zone ? (
        <div className="mb-5">
          <h2 className="font-heading text-[1.8rem] leading-tight text-gold">{zone}</h2>
          <p className="mt-0.5 text-sm text-text-muted">{size === 'raid' ? 'Raid Zone' : 'Dungeon'}</p>
        </div>
      ) : (
        <p className="mb-5 text-text-muted">Choose a raid or dungeon above to see its rankings.</p>
      )}

      {/* Lower — view filters: Type / Boss / Class */}
      {size && zone && (
        <div className="mb-4">
          <FilterBar>
            <FilterDropdown value={metric} options={METRICS} onChange={v => update({ metric: v })} />
            <FilterDropdown
              value={boss}
              placeholder="Boss…"
              options={(zoneObj?.bosses ?? []).map(b => ({ value: b, label: b }))}
              onChange={v => update({ boss: v })}
            />
            {!isSpeed && (
              <FilterDropdown
                value={cls}
                options={[{ value: '', label: 'All classes' }, ...(board?.classes ?? []).map(c => ({ value: c, label: c }))]}
                onChange={v => update({ class: v })}
              />
            )}
          </FilterBar>
        </div>
      )}

      {loading && <p className="text-text-muted">Loading…</p>}
      {!loading && size && zone && boss && board && board.rows.length === 0 && (
        <p className="text-text-muted">No ranked kills recorded for this boss yet.</p>
      )}
      {!loading && board && board.rows.length > 0 && size && zone && boss && (
        <Card className="p-0 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-text-muted text-left text-[0.72rem] uppercase tracking-wide border-b border-border">
                <th className="px-3 py-2">#</th>
                <th className="px-3 py-2">%</th>
                <th className="px-3 py-2">{isSpeed ? 'Guild' : 'Player'}</th>
                {!isSpeed && <th className="px-3 py-2">Lvl</th>}
                {!isSpeed && <th className="px-3 py-2">Class</th>}
                <th className="px-3 py-2 text-right">{isSpeed ? 'Time' : metric === 'hps' ? 'HPS' : 'DPS'}</th>
                <th className="px-3 py-2 text-right" title={isSpeed ? 'Average raid item level' : 'Character item level'}>iLvl</th>
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
                  <td className="px-3 py-2 text-right tabular-nums text-gold">{r.ilvl != null ? Math.round(r.ilvl).toLocaleString() : '—'}</td>
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
