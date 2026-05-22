/**
 * Generic search page shell.
 * Each search page (Character, Guild, Item) is a thin wrapper around
 * this component with its own config.
 */
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

export interface SearchPageConfig {
  title: string
  subtitle: string
  placeholder: string
  /** Return the URL to navigate to on submit, or null to stay on the page */
  getTarget: (query: string) => string | null
}

export function SearchPage({ config }: { config: SearchPageConfig }) {
  const [query, setQuery] = useState('')
  const navigate = useNavigate()

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const q = query.trim()
    if (!q) return
    const target = config.getTarget(q)
    if (target) navigate(target)
  }

  return (
    <main style={{ maxWidth: 600, margin: '4rem auto', padding: '0 1.5rem' }}>
      <Link to="/" style={{ color: 'var(--text-muted)', fontSize: '0.9rem', textDecoration: 'none' }}>
        ← Back
      </Link>

      <div style={{ margin: '1.5rem 0 2rem' }}>
        <h1 style={{
          fontFamily: "'Cinzel', serif",
          fontSize: '1.9rem',
          fontWeight: 700,
          letterSpacing: '0.06em',
          background: 'linear-gradient(135deg, #c8a96e 0%, #e8d5a3 40%, #c8a96e 70%, #a07840 100%)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          backgroundClip: 'text',
          display: 'inline-block',
          marginBottom: '0.35rem',
        }}>
          {config.title}
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', lineHeight: 1.6 }}>
          {config.subtitle}
        </p>
      </div>

      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: '0.6rem' }}>
        <input
          type="text"
          placeholder={config.placeholder}
          value={query}
          onChange={e => setQuery(e.target.value)}
          autoFocus
          style={{ flex: 1, fontSize: '1rem' }}
        />
        <button
          type="submit"
          disabled={!query.trim()}
          style={{
            padding: '0.5rem 1.4rem',
            borderRadius: 6,
            border: '1px solid rgba(var(--accent-rgb,99,210,130),0.4)',
            background: 'rgba(var(--accent-rgb,99,210,130),0.12)',
            color: 'var(--accent)',
            fontSize: '0.95rem',
            fontWeight: 600,
            cursor: 'pointer',
            whiteSpace: 'nowrap',
            opacity: query.trim() ? 1 : 0.45,
          }}
        >
          Search
        </button>
      </form>
    </main>
  )
}

// ── Individual search pages ───────────────────────────────────────────────────

export function CharacterSearchPage() {
  return (
    <SearchPage config={{
      title: 'Character Search',
      subtitle: 'Look up any character on the server — view their stats, equipment, spells and adornments.',
      placeholder: 'Character name…',
      getTarget: q => `/character/${encodeURIComponent(q)}`,
    }} />
  )
}

export function GuildSearchPage() {
  return (
    <SearchPage config={{
      title: 'Guild Search',
      subtitle: 'Browse a guild\'s roster, run spell checks, and view adornment coverage across members.',
      placeholder: 'Guild name…',
      getTarget: q => `/guild/${encodeURIComponent(q)}`,
    }} />
  )
}

