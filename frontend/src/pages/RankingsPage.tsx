import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useFetch } from '../hooks/useFetch'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { Card } from '../components/ui'
import { FilterBar, FilterDropdown, type DropdownOption } from '../components/FilterDropdown'
import { fmtDuration, fmtLocalDate, fmtNum } from '../formatters'
import { percentileColor } from '../percentileColors'
import { useClasses } from '../useClasses'

// Canonical EQ2 archetype order for the grouped class dropdown.
const ARCHETYPE_ORDER = ['Fighter', 'Priest', 'Scout', 'Mage']

/**
 * Build the class-filter dropdown options: "All classes", then — per archetype
 * that has at least one class on this board — a selectable "All <archetype>s"
 * row followed by its individual classes, all sharing the archetype as the
 * FilterDropdown group caption. The archetype value (e.g. "Fighter") is sent as
 * ?class= and expanded server-side (rankings.py:_ARCHETYPE_CLASSES).
 */
export function buildClassOptions(present: string[], archetypeOf: (cls: string) => string | undefined): DropdownOption[] {
  const opts: DropdownOption[] = [{ value: '', label: 'All classes' }]
  if (present.length === 0) return opts

  const byArch = new Map<string, string[]>()
  for (const c of present) {
    const arch = archetypeOf(c) ?? 'Other'
    if (!byArch.has(arch)) byArch.set(arch, [])
    byArch.get(arch)!.push(c)
  }
  const order = (a: string) => {
    const i = ARCHETYPE_ORDER.indexOf(a)
    return i === -1 ? ARCHETYPE_ORDER.length : i
  }
  for (const arch of [...byArch.keys()].sort((a, b) => order(a) - order(b) || a.localeCompare(b))) {
    const group = `${arch}s` // "Fighters", "Mages", …
    opts.push({ value: arch, label: `All ${group}`, group })
    for (const c of byArch.get(arch)!.sort()) opts.push({ value: c, label: c, group })
  }
  return opts
}

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

// Normalise boss-name codepoint variants for membership/equality checks.
// ACT log files, the in-game client, and curator-entered roster data are
// inconsistent about which apostrophe codepoint they use (U+0027 vs U+2019
// vs U+02BC and others) — and similarly for whitespace (NBSP creeps in from
// copy-paste). Backend mirror: _normalise_boss_key in web/routes/rankings.py.
// Codepoints written as explicit \uXXXX escapes so editor/tool re-encoding
// can't silently collapse them to ASCII (which would leave the regex a no-op).
// All apostrophe-like / space-like variants seen in ACT logs or curator data:
const APOSTROPHE_VARIANTS = /[`´ʹʺʻʼʽʾʿˈ‘’‛′＇]/g
const SPACE_VARIANTS = /[     　]/g

export function normaliseBossName(s: string): string {
  return s
    .normalize('NFC')
    .replace(APOSTROPHE_VARIANTS, "'")
    .replace(SPACE_VARIANTS, ' ')
    .trim()
    .toLowerCase()
}

// Metric options grouped Character vs Guild (à la Warcraft Logs).
const METRICS_RAID: DropdownOption[] = [
  { value: 'dps', label: 'Damage (DPS)', group: 'Character' },
  { value: 'hps', label: 'Healing (HPS)', group: 'Character' },
  { value: 'speed', label: 'Speed', group: 'Guild' },
]

// Dungeons (size=group) rank Speed per-player — most dungeons are run
// with mixed-guild groups, so guild attribution is meaningless. The
// dropdown collapses to a single Character group with no Guild label.
const METRICS_GROUP: DropdownOption[] = [
  { value: 'dps', label: 'Damage (DPS)', group: 'Character' },
  { value: 'hps', label: 'Healing (HPS)', group: 'Character' },
  { value: 'speed', label: 'Speed', group: 'Character' },
]

// safeSetParams — call setSearchParams() defensively. Firefox's per-Document
// history-API quota can be depleted by browser extensions (e.g. ClearURLs
// rewriting URLs via webRequest hooks) or by Firefox's own privacy/tracking-
// protection internals running in a sandbox-eval context. When the quota is
// empty, react-router's setSearchParams call throws DOMException SecurityError
// and silently fails. We catch + retry-after-quota-reset so the URL eventually
// catches up to React state. State is the source of truth (see RankingsPage);
// URL sync is best-effort — if it never lands, the page still works fine.
export function safeSetParams(
  setParams: (...args: unknown[]) => void,
  args: unknown[],
): void {
  try {
    setParams(...args)
  } catch (e) {
    if (e instanceof DOMException && (e.name === 'SecurityError' || e.name === 'InvalidStateError')) {
      // Firefox's throttle window is ~10s. Retry after 1.2s in case quota recovers fast.
      // If it still fails, give up — React state is correct, URL is just slightly stale.
      setTimeout(() => {
        try {
          setParams(...args)
        } catch { /* give up silently — page still works, URL is stale */ }
      }, 1200)
    } else {
      throw e
    }
  }
}

export default function RankingsPage() {
  const [filters, setFilters] = useState<FiltersResponse>({ scopes: [] })
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()

  // ── Filter state ─────────────────────────────────────────────────────────
  // React state is the SOURCE OF TRUTH for what the user has selected. The
  // URL is a one-way mirror updated best-effort by the sync effect below.
  // This decouples our render path from external URL-rewriting code (browser
  // extensions like ClearURLs, Firefox internals) that can throttle the
  // history API and break the page if state lived in the URL.
  //
  // Initial values seed from the URL once (lazy initialiser). After mount,
  // URL changes are ignored — clicking the dropdowns mutates state directly.
  // Back/forward navigation within the rankings page is consequently a no-op;
  // acceptable given the alternative is the page locking up.
  const [size, setSize] = useState(() => searchParams.get('size') || '')
  const [zone, setZone] = useState(() => searchParams.get('zone') || '')
  const [boss, setBoss] = useState(() => searchParams.get('boss') || '')
  const [metric, setMetric] = useState(() => searchParams.get('metric') || 'dps')
  const [cls, setCls] = useState(() => searchParams.get('class') || '')
  const { byName: classByName } = useClasses()
  // Explicit ?xpac in URL overrides the zone-derived expansion. null = derive.
  const [xpacOverride, setXpacOverride] = useState<string | null>(
    () => searchParams.get('xpac'),
  )

  // ── Filters fetch ────────────────────────────────────────────────────────
  useEffect(() => {
    fetch('/api/rankings/filters', { credentials: 'include' })
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((j: FiltersResponse) => setFilters(j))
      .catch(() => setFilters({ scopes: [] }))
  }, [])

  // ── Derived dropdown data ────────────────────────────────────────────────
  const scope = filters.scopes.find(s => s.key === size)
  const zoneObj = scope?.zones.find(z => z.zone === zone)
  const raidZones = filters.scopes.find(s => s.key === 'raid')?.zones ?? []
  const groupZones = filters.scopes.find(s => s.key === 'group')?.zones ?? []
  const raidExpansions = filters.raid_expansions ?? []

  // Active expansion: explicit ?xpac, else the selected zone's own expansion
  // (raids OR dungeons — both carry expansion now since dungeons came from
  // the curated zones.db tagging in #36, not from kill data), else the
  // server's default (SERVER_CURRENT_XPAC / most recent).
  const xpac = xpacOverride || zoneObj?.expansion || filters.default_expansion || ''
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

  // ── URL sync (state → URL, best-effort) ──────────────────────────────────
  // Mirror state to the URL whenever it changes. Wrapped in safeSetParams
  // so a throttled history API doesn't break the page — worst case the URL
  // is slightly stale, state remains the source of truth.
  useEffect(() => {
    const next = new URLSearchParams()
    if (size)    next.set('size',   size)
    if (zone)    next.set('zone',   zone)
    if (boss)    next.set('boss',   boss)
    if (metric && metric !== 'dps') next.set('metric', metric)
    if (cls)     next.set('class',  cls)
    if (xpacOverride) next.set('xpac', xpacOverride)
    safeSetParams(setSearchParams as (...args: unknown[]) => void, [next, { replace: true }])
  }, [size, zone, boss, metric, cls, xpacOverride, setSearchParams])

  // ── Click handlers ───────────────────────────────────────────────────────
  // Picking a zone from the top bar also selects its first boss.
  const pickZone = useCallback(
    (scopeKey: string, zoneName: string, zones: FilterZone[]) => {
      const z = zones.find(x => x.zone === zoneName)
      setSize(scopeKey)
      setZone(zoneName)
      setBoss(z?.bosses[0] ?? '')
      // Clear xpac override so the new zone's own expansion drives the filter.
      setXpacOverride(null)
    },
    [],
  )

  // ── Boss-default fallback ────────────────────────────────────────────────
  // If a URL-seeded boss isn't in the current zone's roster (typo, stale link,
  // deleted boss), fall back to the first boss. The loop-breaker is preserved
  // from the old URL-based implementation as defence-in-depth — even though
  // state-based setBoss can't generate the URL round-trip mutation that the
  // original ref was guarding against, an unforeseen render loop would still
  // hit Firefox's renderer hot loop detection.
  const resetCountRef = useRef(0)
  const lastZoneRef = useRef<string>('')
  useEffect(() => {
    if (!size || !zone || !zoneObj || !zoneObj.bosses.length) return

    if (lastZoneRef.current !== zone) {
      lastZoneRef.current = zone
      resetCountRef.current = 0
    }

    const normBoss = normaliseBossName(boss)
    const inList = zoneObj.bosses.some(b => normaliseBossName(b) === normBoss)
    if (boss && inList) {
      resetCountRef.current = 0
      return
    }

    if (resetCountRef.current >= 2) return

    resetCountRef.current += 1
    setBoss(zoneObj.bosses[0])
  }, [size, zone, zoneObj, boss])

  // ── Board fetch ──────────────────────────────────────────────────────────
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
  const metrics = size === 'group' ? METRICS_GROUP : METRICS_RAID

  return (
    <main className="page-enter mx-auto max-w-5xl px-4 py-6">
      {/* Top — category nav: Raids · Expansion · Dungeons · Raid Guides */}
      <div className="mb-5">
        <FilterBar>
          {raidExpansions.length > 0 && (
            <FilterDropdown
              value={xpac}
              options={raidExpansions.map(e => ({ value: e.short, label: e.name }))}
              onChange={v => setXpacOverride(v)}
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
            <FilterDropdown value={metric} options={metrics} onChange={v => setMetric(v)} />
            <FilterDropdown
              value={boss}
              placeholder="Boss…"
              options={(zoneObj?.bosses ?? []).map(b => ({ value: b, label: b }))}
              onChange={v => setBoss(v)}
            />
            {!isSpeed && (
              <FilterDropdown
                value={cls}
                options={buildClassOptions(board?.classes ?? [], c => classByName.get(c)?.archetype)}
                onChange={v => setCls(v)}
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
                <th className="px-3 py-2">{isSpeed && size === 'raid' ? 'Guild' : 'Player'}</th>
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
                    {r.kind === 'guild' ? (
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
