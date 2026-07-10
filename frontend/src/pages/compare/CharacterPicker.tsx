import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Card, SectionLabel } from '../../components/ui'
import { FreshnessBadge } from '../../components/FreshnessBadge'
import { useClasses } from '../../useClasses'
import { useDebounce } from '../../hooks/useDebounce'
import type { FavoriteEntry, SideState } from './types'

const INPUT_CLASS =
  'py-2 px-3 rounded-sm2 border border-border bg-surface-raised text-text text-base leading-[1.4] [color-scheme:dark] w-full'

interface SearchResult {
  name: string
  cls: string | null
  level: number | null
  guild_name: string | null
}

function CandidateRow({ name, cls, level, guildName, disabled, onSelect }: {
  name: string
  cls: string | null
  level: number | null
  guildName: string | null
  disabled: boolean
  onSelect: () => void
}) {
  const { colourFor } = useClasses()
  return (
    <button
      type="button"
      disabled={disabled}
      title={disabled ? 'Already selected on the other side' : undefined}
      onClick={onSelect}
      className={`appearance-none border-0 bg-transparent w-full flex items-baseline gap-2 px-2 py-1.5 rounded-sm text-left ${
        disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer hover:bg-gold/10'
      }`}
    >
      <span className="text-[0.88rem] font-medium" style={{ color: colourFor(cls, 'var(--text)') }}>{name}</span>
      <span className="text-[0.75rem] text-text-muted">
        {[cls, level != null ? `Lv ${level}` : null, guildName ? `<${guildName}>` : null].filter(Boolean).join(' · ')}
      </span>
    </button>
  )
}

/**
 * One compare slot: a chosen-character chip, or a picker panel offering the
 * user's favourites first with a debounced character search fallback.
 */
export default function CharacterPicker({ side, state, favorites, excludeName, onSelect, onClear }: {
  side: 'A' | 'B'
  state: SideState
  favorites: FavoriteEntry[] | null
  /** The other side's chosen name — rendered disabled to block self-compare. */
  excludeName: string | null
  onSelect: (name: string) => void
  onClear: () => void
}) {
  const { colourFor } = useClasses()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[] | null>(null)
  const [searching, setSearching] = useState(false)
  const seqRef = useRef(0)

  const runSearch = useDebounce((q: string) => {
    if (q.trim().length < 2) {
      seqRef.current++ // invalidate any in-flight longer-query response
      setResults(null)
      setSearching(false)
      return
    }
    const seq = ++seqRef.current
    setSearching(true)
    fetch(`/api/characters/search?name=${encodeURIComponent(q.trim())}`, { credentials: 'include' })
      .then(r => (r.ok ? r.json() : { results: [] }))
      .then(d => {
        if (seq !== seqRef.current) return // stale response
        setResults(d.results ?? [])
        setSearching(false)
      })
      .catch(() => { if (seq === seqRef.current) { setResults([]); setSearching(false) } })
  }, 300)

  useEffect(() => {
    if (state.status === 'empty') { setQuery(''); setResults(null) }
  }, [state.status])

  const isExcluded = (name: string) => excludeName != null && name.toLowerCase() === excludeName.toLowerCase()

  // ── Chosen chip ────────────────────────────────────────────────────────────
  if (state.status === 'ok') {
    const c = state.char
    return (
      <Card className="rounded-sm px-4 py-3 relative">
        <button
          type="button"
          onClick={onClear}
          title="Clear"
          className="appearance-none border-0 bg-transparent absolute top-2 right-2.5 text-text-muted hover:text-text cursor-pointer text-[0.9rem] leading-none p-1"
        >
          ✕
        </button>
        <div
          className="font-heading text-[1.25rem] font-bold leading-[1.2]"
          style={{ color: colourFor(c.cls, 'var(--gold)') }}
        >
          {c.name}
        </div>
        <div className="text-[0.8rem] text-text-muted mt-0.5">
          {[c.cls, c.level != null ? `Lv ${c.level}` : null].filter(Boolean).join(' · ') || '—'}
        </div>
        {c.guild_name && <div className="text-[0.78rem] text-gold/70 mt-0.5">&lt;{c.guild_name}&gt;</div>}
        <div className="flex items-center gap-3 mt-1">
          <FreshnessBadge stale={c.stale} />
          <Link to={`/character/${encodeURIComponent(c.name)}`} className="text-[0.75rem] text-text-muted no-underline hover:text-gold">
            view page →
          </Link>
        </div>
      </Card>
    )
  }

  // ── Loading / error states for a chosen-but-unresolved name ────────────────
  if (state.status === 'loading') {
    return <Card className="rounded-sm px-4 py-3 text-text-muted text-[0.85rem]">Loading {state.name}…</Card>
  }

  return (
    <Card className="rounded-sm px-4 py-3">
      {state.status === 'not_found' && (
        <p className="text-[0.82rem] text-danger mt-0 mb-2">Character "{state.name}" not found — pick another.</p>
      )}
      {state.status === 'census_unavailable' && (
        <p className="text-[0.82rem] text-warning mt-0 mb-2">
          "{state.name}" isn't cached yet and Census is unavailable — try again shortly or pick another.
        </p>
      )}
      {state.status === 'error' && (
        <p className="text-[0.82rem] text-danger mt-0 mb-2">Couldn't load "{state.name}": {state.message}</p>
      )}

      <div className="text-[0.78rem] text-text-muted mb-2">Character {side}</div>

      {favorites && favorites.length > 0 && (
        <div className="mb-3">
          <SectionLabel>Favourites</SectionLabel>
          <div className="max-h-[220px] overflow-y-auto">
            {favorites.map(f => (
              <CandidateRow
                key={f.character_name}
                name={f.character_name}
                cls={f.cls}
                level={f.level}
                guildName={f.guild_name}
                disabled={isExcluded(f.character_name)}
                onSelect={() => onSelect(f.character_name)}
              />
            ))}
          </div>
        </div>
      )}

      <SectionLabel>Search</SectionLabel>
      <input
        type="text"
        className={INPUT_CLASS}
        placeholder="Character name (min 2 letters)…"
        value={query}
        onChange={e => { setQuery(e.target.value); runSearch(e.target.value) }}
        aria-label={`Search character ${side}`}
      />
      {searching && <p className="text-[0.78rem] text-text-muted mt-2 mb-0">Searching…</p>}
      {!searching && results !== null && results.length === 0 && (
        <p className="text-[0.78rem] text-text-muted mt-2 mb-0">No characters found.</p>
      )}
      {!searching && results !== null && results.length > 0 && (
        <div className="mt-2 max-h-[260px] overflow-y-auto">
          {results.map(r => (
            <CandidateRow
              key={r.name}
              name={r.name}
              cls={r.cls}
              level={r.level}
              guildName={r.guild_name}
              disabled={isExcluded(r.name)}
              onSelect={() => onSelect(r.name)}
            />
          ))}
        </div>
      )}
    </Card>
  )
}
