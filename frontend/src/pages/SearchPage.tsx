/**
 * Shared live-search page.
 * Character and Guild search both use this component.
 * Results come from the Census API (all characters/guilds on the server),
 * with a fallback to locally-registered data when Census is unavailable.
 */
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import BackLink from '../components/BackLink'

// ── Shared styles ─────────────────────────────────────────────────────────────

const CTRL: React.CSSProperties = {
  padding:      '0.52rem 0.75rem',
  borderRadius: 6,
  border:       '1px solid var(--border)',
  background:   'var(--surface-raised)',
  color:        'var(--text)',
  fontSize:     '1rem',
  lineHeight:   '1.4',
  colorScheme:  'dark',
  flex:         1,
}

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
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

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

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) {
      setResults([])
      setSearched(false)
      setLoading(false)
      return
    }

    // Debounce 300 ms
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
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
    }, 300)

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [query, config.apiUrl])

  return (
    <main style={{ maxWidth: 640, margin: '4rem auto', padding: '0 1.5rem' }}>
      <BackLink />

      {/* Header */}
      <div style={{ margin: '1.25rem 0 1.75rem' }}>
        <h1 style={{
          fontFamily: "'Cinzel', serif",
          fontSize: '1.9rem', fontWeight: 700, letterSpacing: '0.06em',
          margin: '0 0 0.3rem',
          background: 'linear-gradient(135deg, #c8a96e 0%, #e8d5a3 40%, #c8a96e 70%, #a07840 100%)',
          WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
          backgroundClip: 'text', display: 'inline-block',
        }}>
          {config.title}
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', lineHeight: 1.6, margin: 0 }}>
          {config.subtitle}
        </p>
      </div>

      {/* Input */}
      <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'center', marginBottom: '0.4rem' }}>
        <input
          type="text"
          placeholder={config.placeholder}
          value={query}
          onChange={e => setQuery(e.target.value)}
          autoFocus
          style={CTRL}
        />
        {loading && (
          <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem', whiteSpace: 'nowrap' }}>
            Searching…
          </span>
        )}
      </div>

      <p style={{ color: 'var(--text-muted)', fontSize: '0.78rem', margin: '0 0 1.25rem' }}>
        {config.sourceNote}
      </p>

      {/* Results */}
      {searched && !loading && results.length === 0 && (
        <p style={{ color: 'var(--text-muted)', fontStyle: 'italic', fontSize: '0.9rem' }}>
          No results for "{query.trim()}".
        </p>
      )}

      {results.length > 0 && (
        <div style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 8,
          overflow: 'hidden',
        }}>
          {results.map((r, i) => {
            const sub = config.renderSub ? config.renderSub(r) : null
            return (
              <Link
                key={r.name}
                to={`${config.linkPrefix}${encodeURIComponent(r.name)}`}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.6rem',
                  padding: '0.6rem 1rem',
                  borderBottom: i < results.length - 1 ? '1px solid var(--border)' : 'none',
                  textDecoration: 'none',
                  transition: 'background 0.1s',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface-raised)')}
                onMouseLeave={e => (e.currentTarget.style.background = '')}
              >
                <span style={{ color: config.linkColor, fontWeight: 500, fontSize: '0.95rem' }}>
                  {r.name}
                </span>
                {sub && (
                  <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem', fontWeight: 400 }}>
                    {sub}
                  </span>
                )}
                <span style={{
                  marginLeft: 'auto',
                  fontSize: '0.75rem',
                  color: 'var(--text-muted)',
                  fontWeight: 400,
                }}>
                  →
                </span>
              </Link>
            )
          })}
        </div>
      )}

      {!searched && query.trim().length > 0 && query.trim().length < 2 && (
        <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
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
      linkColor:   '#c8a96e',
      sourceNote:  'Searches guilds tracked on this site.',
    }} />
  )
}
