import { useRef, useState } from 'react'
import { useClasses } from '../../useClasses'
import { useDebounce } from '../../hooks/useDebounce'

// Compact debounced character-name search with a suggestion dropdown —
// shared by the group make-up panel (add a member) and the raid-buffs
// panel (attach a buff supplier).

export interface CharacterSearchResult {
  name: string
  cls: string | null
  level: number | null
  guild_name: string | null
}

export default function CharacterSearchInput({ placeholder, exclude, disabled, compact, onPick }: {
  placeholder: string
  /** Names never offered (already added, or the simmed character). */
  exclude: string[]
  disabled?: boolean
  /** Smaller input for inline rows (the buff-supplier slot). */
  compact?: boolean
  onPick: (r: CharacterSearchResult) => void
}) {
  const { colourFor } = useClasses()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<CharacterSearchResult[] | null>(null)
  const [searching, setSearching] = useState(false)
  const seqRef = useRef(0)

  const runSearch = useDebounce((q: string) => {
    if (q.trim().length < 2) {
      seqRef.current++
      setResults(null)
      setSearching(false)
      return
    }
    const seq = ++seqRef.current
    setSearching(true)
    fetch(`/api/characters/search?name=${encodeURIComponent(q.trim())}`, { credentials: 'include' })
      .then(r => (r.ok ? r.json() : { results: [] }))
      .then(d => {
        if (seq !== seqRef.current) return
        setResults(d.results ?? [])
        setSearching(false)
      })
      .catch(() => { if (seq === seqRef.current) { setResults([]); setSearching(false) } })
  }, 300)

  const lowerExclude = exclude.map(n => n.toLowerCase())
  const addable = (results ?? []).filter(r => !lowerExclude.includes(r.name.toLowerCase()))
  const pick = (r: CharacterSearchResult) => {
    onPick(r)
    setQuery('')
    setResults(null)
    seqRef.current++
  }

  return (
    <span className={`relative ${compact ? 'inline-block' : 'block'}`}>
      <input
        type="text"
        value={query}
        placeholder={placeholder}
        disabled={disabled}
        onChange={e => { setQuery(e.target.value); runSearch(e.target.value) }}
        className={
          compact
            ? 'py-1 px-2 rounded-sm2 border border-border bg-surface text-text text-[0.78rem] w-44 [color-scheme:dark]'
            : 'w-full py-1.5 px-2 rounded-sm2 border border-border bg-surface-raised text-text text-[0.85rem] [color-scheme:dark]'
        }
        aria-label={placeholder}
      />
      {searching && (
        <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[0.7rem] text-text-muted">…</span>
      )}
      {results !== null && query.trim().length >= 2 && (
        <div className="absolute z-dropdown left-0 right-0 mt-1 rounded-sm border border-border bg-surface shadow-lg max-h-56 overflow-y-auto min-w-56">
          {addable.length === 0 && !searching && (
            <p className="text-[0.78rem] text-text-muted px-3 py-2 m-0">No matching characters.</p>
          )}
          {addable.map(r => (
            <button
              key={r.name}
              type="button"
              onClick={() => pick(r)}
              className="appearance-none border-0 bg-transparent w-full text-left px-3 py-1.5 cursor-pointer hover:bg-gold/10 text-[0.85rem] text-text flex items-baseline gap-2"
            >
              <span className="font-medium">{r.name}</span>
              <span className="text-[0.75rem]" style={{ color: colourFor(r.cls ?? '') }}>
                {r.cls ?? '?'}
              </span>
              {r.level != null && <span className="text-[0.72rem] text-text-muted">lv {r.level}</span>}
              {r.guild_name && <span className="text-[0.72rem] text-text-muted truncate">&lt;{r.guild_name}&gt;</span>}
            </button>
          ))}
        </div>
      )}
    </span>
  )
}
