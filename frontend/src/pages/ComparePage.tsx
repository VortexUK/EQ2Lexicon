import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import Breadcrumb from '../components/Breadcrumb'
import { Button, Card } from '../components/ui'
import { TabButton } from '../components/ui/TabButton'
import { useClasses } from '../useClasses'
import { useFetch } from '../hooks/useFetch'
import { mergeParams, safeSetParams } from '../lib/searchParams'
import { fetchCharacter } from '../lib/characterCache'
import CharacterPicker from './compare/CharacterPicker'
import CompareStats from './compare/CompareStats'
import CompareGear from './compare/CompareGear'
import CompareAAs from './compare/CompareAAs'
import DeltaChip from './compare/DeltaChip'
import { nullableDelta } from './compare/diff'
import type { CompareTab, FavoriteEntry, SideState } from './compare/types'

const TABS: [CompareTab, string][] = [
  ['stats', 'Stats'],
  ['gear', 'Gear'],
  ['aas', 'AAs'],
]

/** Resolve one side: fetch (cache-first, shared with the character page) and
 * track per-side status. Sides resolve independently. */
function useCompareCharacter(name: string | null): SideState {
  const [state, setState] = useState<SideState>({ status: 'empty' })

  useEffect(() => {
    if (!name) {
      setState({ status: 'empty' })
      return
    }
    let cancelled = false
    setState({ status: 'loading', name })
    fetchCharacter(name).then(result => {
      if (cancelled) return
      switch (result.status) {
        case 'ok':                 setState({ status: 'ok', char: result.char }); break
        case 'not_found':          setState({ status: 'not_found', name }); break
        case 'census_unavailable': setState({ status: 'census_unavailable', name }); break
        case 'error':              setState({ status: 'error', name, message: result.message }); break
      }
    })
    return () => { cancelled = true }
  }, [name])

  return state
}

export default function ComparePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { colourFor } = useClasses()

  // React state is the source of truth; the URL is a best-effort mirror
  // (safeSetParams — survives the Firefox History-API throttle). Initialised
  // once from ?a/?b; a self-compare deep link drops b.
  const [names, setNames] = useState<{ a: string | null; b: string | null }>(() => {
    const a = searchParams.get('a')
    let b = searchParams.get('b')
    if (a && b && a.toLowerCase() === b.toLowerCase()) b = null
    return { a: a || null, b: b || null }
  })
  const [tab, setTab] = useState<CompareTab>(() => {
    const t = searchParams.get('tab')
    return t === 'gear' || t === 'aas' ? t : 'stats'
  })

  useEffect(() => {
    safeSetParams(setSearchParams as (...args: unknown[]) => void, [
      mergeParams({ a: names.a, b: names.b, tab: tab === 'stats' ? null : tab }),
      { replace: true },
    ])
  }, [names, tab, setSearchParams])

  const sideA = useCompareCharacter(names.a)
  const sideB = useCompareCharacter(names.b)

  // Favourites feed the pickers (favourites-first); errors/empty degrade to
  // search-only silently.
  const { data: favData } = useFetch<{ favorites: FavoriteEntry[] }>('/api/favorites')
  const favorites = favData?.favorites ?? null

  const select = (side: 'a' | 'b') => (name: string) => {
    setNames(prev => {
      const other = side === 'a' ? prev.b : prev.a
      if (other && other.toLowerCase() === name.toLowerCase()) return prev // block self-compare
      return { ...prev, [side]: name }
    })
  }
  const clear = (side: 'a' | 'b') => () => setNames(prev => ({ ...prev, [side]: null }))
  const swap = () => setNames(prev => ({ a: prev.b, b: prev.a }))

  const bothOk = sideA.status === 'ok' && sideB.status === 'ok'
  const charA = sideA.status === 'ok' ? sideA.char : null
  const charB = sideB.status === 'ok' ? sideB.char : null

  return (
    <main className="max-w-[1100px] my-8 mx-auto px-4">
      <Breadcrumb items={[{ label: 'Characters', to: '/characters' }, { label: 'Compare' }]} />

      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 mt-2 mb-4">
        <h1 className="font-heading text-[1.5rem] font-bold text-gold m-0">Compare Characters</h1>
        {bothOk && charA && charB && (
          <span className="text-[0.75rem] text-text-muted border border-border rounded-full px-2.5 py-0.5">
            Δ = <span style={{ color: colourFor(charB.cls, 'var(--text)') }}>{charB.name}</span>
            {' − '}
            <span style={{ color: colourFor(charA.cls, 'var(--text)') }}>{charA.name}</span>
          </span>
        )}
      </div>

      {/* Pickers */}
      <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] gap-3 items-start mb-5">
        <CharacterPicker
          side="A"
          state={sideA}
          favorites={favorites}
          excludeName={names.b}
          onSelect={select('a')}
          onClear={clear('a')}
        />
        <div className="flex md:flex-col items-center justify-center gap-2 md:pt-6">
          <Button variant="ghost" size="sm" onClick={swap} title="Swap sides" disabled={!names.a && !names.b}>
            ⇄ swap
          </Button>
        </div>
        <CharacterPicker
          side="B"
          state={sideB}
          favorites={favorites}
          excludeName={names.a}
          onSelect={select('b')}
          onClear={clear('b')}
        />
      </div>

      {/* One side picked, other empty → prompt */}
      {!bothOk && (sideA.status === 'ok' || sideB.status === 'ok') && (
        <p className="text-text-muted text-[0.9rem] text-center mt-8">
          Pick a second character to compare.
        </p>
      )}

      {bothOk && charA && charB && (
        <>
          {/* Overview strip */}
          <Card className="rounded-sm px-4 py-2.5 mb-4 flex flex-wrap items-baseline gap-x-8 gap-y-1 text-[0.85rem]">
            <span>
              <span className="text-text-muted">Level:</span>{' '}
              <span className="font-semibold tabular-nums">{charA.level ?? '—'}</span>
              <span className="text-text-muted"> vs </span>
              <span className="font-semibold tabular-nums">{charB.level ?? '—'}</span>{' '}
              <DeltaChip delta={nullableDelta(charA.level, charB.level)} fmt="int" />
            </span>
            <span>
              <span className="text-text-muted">Item Level:</span>{' '}
              <span className="font-semibold tabular-nums">{charA.ilvl != null ? Math.round(charA.ilvl) : '—'}</span>
              <span className="text-text-muted"> vs </span>
              <span className="font-semibold tabular-nums">{charB.ilvl != null ? Math.round(charB.ilvl) : '—'}</span>{' '}
              <DeltaChip
                delta={nullableDelta(
                  charA.ilvl != null ? Math.round(charA.ilvl) : null,
                  charB.ilvl != null ? Math.round(charB.ilvl) : null,
                )}
                fmt="int"
              />
            </span>
            <span>
              <span className="text-text-muted">AAs:</span>{' '}
              <span className="font-semibold tabular-nums">{charA.aa_count}</span>
              <span className="text-text-muted"> vs </span>
              <span className="font-semibold tabular-nums">{charB.aa_count}</span>{' '}
              <DeltaChip delta={charB.aa_count - charA.aa_count} fmt="int" />
            </span>
            <span>
              <span className="text-text-muted">Health:</span>{' '}
              <span className="font-semibold tabular-nums">{charA.stats.health_max?.toLocaleString() ?? '—'}</span>
              <span className="text-text-muted"> vs </span>
              <span className="font-semibold tabular-nums">{charB.stats.health_max?.toLocaleString() ?? '—'}</span>{' '}
              <DeltaChip delta={nullableDelta(charA.stats.health_max, charB.stats.health_max)} fmt="int" />
            </span>
          </Card>

          {/* Tabs */}
          <div className="flex border-b border-border mb-4">
            {TABS.map(([key, label]) => (
              <TabButton key={key} active={tab === key} onClick={() => setTab(key)}>
                {label}
              </TabButton>
            ))}
          </div>

          {tab === 'stats' && <CompareStats charA={charA} charB={charB} />}
          {tab === 'gear' && <CompareGear charA={charA} charB={charB} />}
          {tab === 'aas' && <CompareAAs charA={charA} charB={charB} />}
        </>
      )}
    </main>
  )
}
