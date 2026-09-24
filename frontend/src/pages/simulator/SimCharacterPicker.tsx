import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Card, SectionLabel } from '../../components/ui'
import { useClasses } from '../../useClasses'
import { useDebounce } from '../../hooks/useDebounce'

// Search-only character slot for the simulator, modeled on
// pages/compare/CharacterPicker (deliberate small duplication — follow-up
// filed to unify the two once this page settles).

const INPUT_CLASS =
  'py-2 px-3 rounded-sm2 border border-border bg-surface-raised text-text text-base leading-[1.4] [color-scheme:dark] w-full'

interface SearchResult {
  name: string
  cls: string | null
  level: number | null
  guild_name: string | null
}

export interface ChosenCharacter {
  name: string
  cls: string | null
  level: number | null
}

export default function SimCharacterPicker({ chosen, loading, error, onSelect, onClear }: {
  chosen: ChosenCharacter | null
  /** True while the chosen character's data is being fetched. */
  loading: boolean
  error: string | null
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

  if (chosen) {
    return (
      <Card className="rounded-sm px-4 py-3 relative">
        <button
          type="button"
          onClick={() => { setQuery(''); setResults(null); onClear() }}
          title="Clear"
          className="appearance-none border-0 bg-transparent absolute top-2 right-2.5 text-text-muted hover:text-text cursor-pointer text-[0.9rem] leading-none p-1"
        >
          ✕
        </button>
        <div
          className="font-heading text-[1.25rem] font-bold leading-[1.2]"
          style={{ color: colourFor(chosen.cls, 'var(--gold)') }}
        >
          {chosen.name}
        </div>
        <div className="text-[0.8rem] text-text-muted mt-0.5">
          {[chosen.cls, chosen.level != null ? `Lv ${chosen.level}` : null].filter(Boolean).join(' · ') || '—'}
        </div>
        <div className="flex items-center gap-3 mt-1">
          {loading && <span className="text-[0.75rem] text-text-muted">Loading abilities…</span>}
          {error && <span className="text-[0.75rem] text-danger">{error}</span>}
          <Link to={`/character/${encodeURIComponent(chosen.name)}`} className="text-[0.75rem] text-text-muted no-underline hover:text-gold">
            view page →
          </Link>
        </div>
      </Card>
    )
  }

  return (
    <Card className="rounded-sm px-4 py-3">
      <SectionLabel>Character</SectionLabel>
      <input
        type="text"
        className={INPUT_CLASS}
        placeholder="Character name (min 2 letters)…"
        value={query}
        onChange={e => { setQuery(e.target.value); runSearch(e.target.value) }}
        aria-label="Search character"
      />
      {searching && <p className="text-[0.78rem] text-text-muted mt-2 mb-0">Searching…</p>}
      {!searching && results !== null && results.length === 0 && (
        <p className="text-[0.78rem] text-text-muted mt-2 mb-0">No characters found.</p>
      )}
      {!searching && results !== null && results.length > 0 && (
        <div className="mt-2 max-h-[260px] overflow-y-auto">
          {results.map(r => (
            <button
              key={r.name}
              type="button"
              onClick={() => onSelect(r.name)}
              className="appearance-none border-0 bg-transparent w-full flex items-baseline gap-2 px-2 py-1.5 rounded-sm text-left cursor-pointer hover:bg-gold/10"
            >
              <span className="text-[0.88rem] font-medium" style={{ color: colourFor(r.cls, 'var(--text)') }}>{r.name}</span>
              <span className="text-[0.75rem] text-text-muted">
                {[r.cls, r.level != null ? `Lv ${r.level}` : null, r.guild_name ? `<${r.guild_name}>` : null].filter(Boolean).join(' · ')}
              </span>
            </button>
          ))}
        </div>
      )}
    </Card>
  )
}
