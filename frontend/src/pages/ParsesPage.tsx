import { useState, useEffect, useMemo, useCallback } from 'react'
import { useFetch } from '../hooks/useFetch'
import { useDebounce } from '../hooks/useDebounce'
import { useSearchParams } from 'react-router-dom'

import { FilterPill } from '../components/FilterPill'
import { fmtLocalDate } from '../formatters'

import { GuildSection } from './parses/GuildSection'
import { NO_GUILD } from './parses/types'
import type { ParseEncounterSummary, ParsesListResponse } from './parses/types'

// ── Types ─────────────────────────────────────────────────────────────────────

type SizeFilter = '' | 'individual' | 'group' | 'raid12' | 'raid24'

// ── Constants ─────────────────────────────────────────────────────────────────

const SIZE_OPTIONS: { value: SizeFilter; label: string; range: string }[] = [
  { value: '',           label: 'All sizes',  range: '' },
  { value: 'raid24',     label: 'Raid (24)',  range: '13–24' },
  { value: 'raid12',     label: 'Raid (12)',  range: '7–12'  },
  { value: 'group',      label: 'Group',      range: '2–6'   },
  { value: 'individual', label: 'Individual', range: '1'     },
]

const PARSES_FETCH_LIMIT = 500

// Visible joiner (used in display) + internal joiner (used in Map keys).
const KEY_SEP = String.fromCharCode(31)   // ASCII Unit Separator — never appears in zone names / dates
const DISPLAY_SEP = ' · '                  // " · "

// ── Helpers ───────────────────────────────────────────────────────────────────

// EQ2 mob naming convention: trash is "a krait warrior" / "an ancient guard"
// (article + lowercase noun), bosses have a proper capitalised name
// ("Captain Krasniv", "The Shadowed One"). First-character capitalisation is
// the simplest reliable signal.
export function isBoss(title: string): boolean {
  return /^[A-Z]/.test(title)
}

// ── Grouped structure ─────────────────────────────────────────────────────────
// Guild → Category (Raid / Dungeon / Other) → ParseEncounterSummary[]
//
// Mirror grouping (collapsing multiple raider uploads of the same fight)
// happens server-side. Each ParseEncounterSummary IS a fight, with the
// canonical upload's fields at the top level. The frontend buckets fights
// by guild then by the backend-computed category.

type Category = 'raid' | 'dungeon' | 'other'

interface ZoneDayBucket {
  key: string                          // "2026-05-24 · Castle Mistmoore"
  date: string                         // local YYYY-MM-DD
  zone: string                         // "Castle Mistmoore" or "(unknown zone)"
  fights: ParseEncounterSummary[]      // sorted started_at desc within the bucket
}

interface GuildBucket {
  guild: string                                // "Exordium" or NO_GUILD
  fightsByCategory: Record<Category, ZoneDayBucket[]>  // each category's zone-day buckets, newest bucket first
  totalFights: number
}

function groupEncounters(fights: ParseEncounterSummary[]): GuildBucket[] {
  // First pass: bucket by guild → category → (date · zone).
  // byGuild[guild][category] is a Map<zoneKey, fights[]> so we can
  // accumulate without intermediate object spreads.
  const byGuild = new Map<
    string,
    Record<Category, Map<string, ParseEncounterSummary[]>>
  >()

  for (const e of fights) {
    const guild = e.guild_name || NO_GUILD
    let cats = byGuild.get(guild)
    if (!cats) {
      cats = { raid: new Map(), dungeon: new Map(), other: new Map() }
      byGuild.set(guild, cats)
    }
    const date = fmtLocalDate(e.started_at)
    const zone = e.zone || '(unknown zone)'
    const zoneKey = [date, zone].join(KEY_SEP)
    let zoneFights = cats[e.category].get(zoneKey)
    if (!zoneFights) {
      zoneFights = []
      cats[e.category].set(zoneKey, zoneFights)
    }
    zoneFights.push(e)
  }

  // Second pass: materialise the ZoneDayBucket arrays, sort within and
  // between buckets.
  const result: GuildBucket[] = []
  for (const [guild, cats] of byGuild) {
    const byCategory: Record<Category, ZoneDayBucket[]> = {
      raid: [],
      dungeon: [],
      other: [],
    }
    let total = 0
    for (const k of ['raid', 'dungeon', 'other'] as const) {
      const buckets: ZoneDayBucket[] = []
      for (const [key, fightsInBucket] of cats[k]) {
        // Server returns fights newest-first overall; re-sort within the
        // bucket so the most recent fight shows on top.
        fightsInBucket.sort((a, b) => b.started_at - a.started_at)
        const [date, zone] = key.split(KEY_SEP)
        buckets.push({
          key: `${date}${DISPLAY_SEP}${zone}`,
          date,
          zone,
          fights: fightsInBucket,
        })
      }
      // Buckets sorted by their newest fight (desc) so the most recent
      // raid night appears first under each category.
      buckets.sort((a, b) => (b.fights[0]?.started_at ?? 0) - (a.fights[0]?.started_at ?? 0))
      byCategory[k] = buckets
      total += buckets.reduce((acc, b) => acc + b.fights.length, 0)
    }
    result.push({ guild, fightsByCategory: byCategory, totalFights: total })
  }

  // Sort guilds: NO_GUILD always last; everyone else by total fight count
  // desc (most-active guild first), with name ASC as tiebreaker.
  result.sort((a, b) => {
    if (a.guild === NO_GUILD) return 1
    if (b.guild === NO_GUILD) return -1
    if (b.totalFights !== a.totalFights) return b.totalFights - a.totalFights
    return a.guild.localeCompare(b.guild)
  })
  return result
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ParsesPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  const [size, setSize] = useState<SizeFilter>(
    (searchParams.get('size') as SizeFilter) ?? '',
  )
  const [bossesOnly, setBossesOnly] = useState<boolean>(
    searchParams.get('bosses') === '1',
  )
  // `searchInput` is the live text field; `search` is the debounced value that
  // actually drives the fetch URL (server-side filter across all stored parses).
  const [searchInput, setSearchInput] = useState<string>(searchParams.get('q') ?? '')
  const [search, setSearch] = useState<string>(searchParams.get('q') ?? '')
  const commitSearch = useDebounce((v: string) => setSearch(v.trim()), 350)

  const parsesUrl = useMemo(() => {
    const url = new URL('/api/parses', window.location.origin)
    if (size) url.searchParams.set('size', size)
    if (search) url.searchParams.set('search', search)
    url.searchParams.set('limit', String(PARSES_FETCH_LIMIT))
    return url.toString()
  }, [size, search])

  const { data: fetchedData, loading, error } = useFetch<ParsesListResponse>(parsesUrl)

  // Local copy for optimistic deletions — seeded from fetchedData on each
  // successful fetch, then mutated locally so deletes don't trigger a full
  // reload (which would unmount GuildSection / CategorySection, losing open state).
  const [localData, setLocalData] = useState<ParsesListResponse | null>(null)
  useEffect(() => {
    if (fetchedData !== null) setLocalData(fetchedData)
  }, [fetchedData])
  const data = localData ?? fetchedData

  // URL sync
  useEffect(() => {
    const p: Record<string, string> = {}
    if (size) p.size = size
    if (bossesOnly) p.bosses = '1'
    if (search) p.q = search
    setSearchParams(p, { replace: true })
  }, [size, bossesOnly, search, setSearchParams])

  // Optimistic local removal after a successful delete — avoids a full
  // refetch (which would briefly toggle `loading` and unmount every
  // GuildSection / CategorySection, losing their open/closed state).
  const removeEncounters = useCallback((pred: (e: ParseEncounterSummary) => boolean) => {
    setLocalData(prev => {
      if (!prev) return prev
      const kept = prev.results.filter(e => !pred(e))
      const removed = prev.results.length - kept.length
      return { results: kept, total: Math.max(0, prev.total - removed) }
    })
  }, [])

  const grouped = useMemo(() => {
    if (!data) return []
    const filtered = bossesOnly
      ? data.results.filter(e => isBoss(e.title))
      : data.results
    return groupEncounters(filtered)
  }, [data, bossesOnly])


  return (
    <main className="max-w-[1100px] mx-auto px-4 py-6">
      <div className="flex items-baseline gap-4 mb-4">
        <h1 className="font-heading text-[1.7rem] text-gold m-0">
          Parses
        </h1>
        {data && (
          <span className="text-[0.82rem] text-text-muted">
            {data.total.toLocaleString()} encounter{data.total !== 1 ? 's' : ''}{size && ' (filtered)'}
          </span>
        )}
      </div>

      {/* Search box — server-side filter across all stored parses */}
      <input
        type="search"
        value={searchInput}
        onChange={e => { setSearchInput(e.target.value); commitSearch(e.target.value) }}
        placeholder="Search by encounter, zone, or uploader…"
        aria-label="Search parses"
        className="w-full mb-3 bg-surface border border-border rounded-sm px-3 py-2 text-[0.875rem] text-text"
      />

      {/* Filter pills */}
      <div className="flex flex-wrap gap-1.5 mb-[1.2rem]">
        {SIZE_OPTIONS.map(opt => (
          <FilterPill key={opt.value || 'all'} active={size === opt.value} onClick={() => setSize(opt.value)}>
            {opt.label}
            {opt.range && <span className="ml-[0.35rem] opacity-60 text-[0.72rem]">{opt.range}</span>}
          </FilterPill>
        ))}
        <span className="w-px bg-border mx-[0.2rem]" />
        <FilterPill
          active={bossesOnly}
          onClick={() => setBossesOnly(v => !v)}
          title="Hide trash mobs (titles starting with 'a' / 'an')"
        >
          Bosses only
        </FilterPill>
      </div>

      {loading && <p className="text-text-muted">Loading…</p>}
      {error && <p className="text-danger">{error}</p>}

      {!loading && !error && data && data.results.length === 0 && (
        <p className="text-text-muted">
          No parses {search ? `match “${search}”` : size ? `match the ${size} filter` : 'yet'}.
        </p>
      )}

      {!loading && grouped.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {grouped.map(g => (
            <GuildSection
              key={g.guild}
              bucket={g}
              defaultExpanded={grouped.length === 1}
              onDeleted={removeEncounters}
            />
          ))}
        </div>
      )}
    </main>
  )
}
