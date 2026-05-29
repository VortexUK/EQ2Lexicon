/**
 * Shared live-search page.
 * Character and Guild search both use this component.
 * Results come from the Census API (all characters/guilds on the server),
 * with a fallback to locally-registered data when Census is unavailable.
 */
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Card } from '../components/ui'
import { useDebounce } from '../hooks/useDebounce'

// ── Timing constants ─────────────────────────────────────────────────────────

const SEARCH_DEBOUNCE_MS = 300

// ── Shared styles ─────────────────────────────────────────────────────────────

const CTRL_CLASS =
  'py-2 px-3 rounded-sm2 border border-border bg-surface-raised text-text text-base leading-[1.4] [color-scheme:dark] flex-1'

// ── Generic live-search shell ─────────────────────────────────────────────────

interface SearchResult {
  name:       string
  cls?:       string | null
  level?:     number | null
  guild_name?: string | null
}

interface SearchConfig {
  title:       string
  subtitle:    string
  placeholder: string
  /** API endpoint, should accept ?name= query param and return {results:[{name,...}], total} */
  apiUrl:      string
  /** URL prefix for result links, e.g. "/character/" */
  linkPrefix:  string
  /** Colour for result links */
  linkColor:   string
  /** Small note rendered below the search input */
  sourceNote:  string
  /** Optional: format a subtitle line from a result row */
  renderSub?:  (r: SearchResult) => string | null
}

function NameSearchPage({ config }: { config: SearchConfig }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const [query,   setQuery]   = useState(() => searchParams.get('q') ?? '')
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)

  // Keep URL in sync so Back navigation restores the query
  useEffect(() => {
    const q = query.trim()
    const current = searchParams.get('q') ?? ''
    if (q === current) return
    if (q) {
      setSearchParams({ q }, { replace: true })
    } else {
      setSearchParams({}, { replace: true })
    }
  }, [query]) // eslint-disable-line react-hooks/exhaustive-deps

  const doSearch = useDebounce(async (q: string) => {
    setLoading(true)
    setSearched(true)
    try {
      const res = await fetch(
        `${config.apiUrl}?name=${encodeURIComponent(q)}`,
        { credentials: 'include' },
      )
      if (res.ok) {
        const data = await res.json()
        setResults(data.results ?? [])
      } else {
        setResults([])
      }
    } catch {
      setResults([])
    } finally {
      setLoading(false)
    }
  }, SEARCH_DEBOUNCE_MS)

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) {
      setResults([])
      setSearched(false)
      setLoading(false)
      return
    }
    doSearch(q)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, config.apiUrl])

  return (
    <main className="max-w-[640px] my-8 md:my-16 mx-auto px-6">
      {/* Header */}
      <div className="mt-5 mb-7">
        <h1
          className="font-heading text-[1.9rem] font-bold tracking-[0.06em] mt-0 mx-0 mb-1 inline-block"
          style={{
            background: 'linear-gradient(135deg, var(--gold) 0%, var(--gold-bright) 40%, var(--gold) 70%, var(--gold-dim) 100%)',
            WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
            backgroundClip: 'text',
          }}
        >
          {config.title}
        </h1>
        <p className="text-text-muted text-[0.9rem] leading-relaxed m-0">
          {config.subtitle}
        </p>
      </div>

      {/* Input */}
      <div className="flex gap-2.5 items-center mb-1.5">
        <input
          type="text"
          placeholder={config.placeholder}
          value={query}
          onChange={e => setQuery(e.target.value)}
          autoFocus
          className={CTRL_CLASS}
        />
        {loading && (
          <span className="text-text-muted text-[0.85rem] whitespace-nowrap">
            Searching…
          </span>
        )}
      </div>

      <p className="text-text-muted text-[0.78rem] mt-0 mx-0 mb-5">
        {config.sourceNote}
      </p>

      {/* Results */}
      {searched && !loading && results.length === 0 && (
        <p className="text-text-muted italic text-[0.9rem]">
          No results for "{query.trim()}".
        </p>
      )}

      {results.length > 0 && (
        <Card className="p-0 overflow-hidden">
          {results.map((r, i) => {
            const sub = config.renderSub ? config.renderSub(r) : null
            return (
              <Link
                key={r.name}
                to={`${config.linkPrefix}${encodeURIComponent(r.name)}`}
                className="flex items-center gap-2.5 py-2.5 px-4 no-underline transition-colors hover:bg-surface-raised"
                style={{
                  borderBottom: i < results.length - 1 ? '1px solid var(--border)' : 'none',
                }}
              >
                <span className="font-medium text-[0.95rem]" style={{ color: config.linkColor }}>
                  {r.name}
                </span>
                {sub && (
                  <span className="text-text-muted text-[0.8rem] font-normal">
                    {sub}
                  </span>
                )}
                <span className="ml-auto text-[0.75rem] text-text-muted font-normal">
                  →
                </span>
              </Link>
            )
          })}
        </Card>
      )}

      {!searched && query.trim().length > 0 && query.trim().length < 2 && (
        <p className="text-text-muted text-[0.85rem]">
          Type at least 2 characters to search.
        </p>
      )}
    </main>
  )
}

// ── Individual search pages ───────────────────────────────────────────────────

export function CharacterSearchPage() {
  return (
    <NameSearchPage config={{
      title:       'Character Search',
      subtitle:    'Find a character by name and view their stats, equipment, spells and adornments.',
      placeholder: 'Character name…',
      apiUrl:      '/api/characters/search',
      linkPrefix:  '/character/',
      linkColor:   'var(--accent)',
      sourceNote:  `Searching all characters on the server. Type a name to begin.`,
      renderSub:   r => {
        const parts: string[] = []
        if (r.cls)   parts.push(r.cls)
        if (r.level) parts.push(`Level ${r.level}`)
        if (r.guild_name) parts.push(r.guild_name)
        return parts.length ? parts.join(' · ') : null
      },
    }} />
  )
}

export function GuildSearchPage() {
  return (
    <NameSearchPage config={{
      title:       'Guild Search',
      subtitle:    "Browse a guild's roster, run spell checks, and view adornment coverage.",
      placeholder: 'Guild name…',
      apiUrl:      '/api/guilds/search',
      linkPrefix:  '/guild/',
      linkColor:   'var(--gold)',
      sourceNote:  'Searches guilds tracked on this site.',
    }} />
  )
}
