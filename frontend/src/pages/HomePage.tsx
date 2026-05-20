import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { avatarUrl, useAuth } from '../hooks/useAuth'
import { useClaim } from '../hooks/useClaim'

async function logout() {
  await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
  window.location.reload()
}

// ── Claim status strip ────────────────────────────────────────────────────────

function ClaimStrip() {
  const claimState = useClaim()

  if (claimState.status === 'loading' || claimState.status === 'unauthenticated') return null
  if (claimState.status === 'error') return null

  const { approved, pending } = claimState.data
  const primary = approved.find(c => c.is_primary === 1) ?? approved[0] ?? null
  const alts = approved.filter(c => c !== primary)

  // No claims at all
  if (approved.length === 0 && !pending) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginTop: '0.75rem' }}>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.88rem' }}>No character claimed.</span>
        <Link to="/claim" style={linkBtn}>Claim character</Link>
      </div>
    )
  }

  return (
    <div style={{ marginTop: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
      {/* Primary character — prominent */}
      {primary && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
          <Link
            to={`/character/${encodeURIComponent(primary.character_name)}`}
            style={{ ...linkBtn, background: 'var(--accent)', fontWeight: 600, fontSize: '0.95rem' }}
          >
            {primary.character_name}
          </Link>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>Primary</span>
          <Link to="/claim" style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginLeft: 'auto' }}>manage</Link>
        </div>
      )}

      {/* Alt characters — smaller */}
      {alts.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap', paddingLeft: '0.25rem' }}>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Alts:</span>
          {alts.map(c => (
            <Link
              key={c.id}
              to={`/character/${encodeURIComponent(c.character_name)}`}
              style={{ ...linkBtn, fontSize: '0.82rem', padding: '0.25rem 0.7rem' }}
            >
              {c.character_name}
            </Link>
          ))}
        </div>
      )}

      {/* Pending claim */}
      {pending && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <span style={{ fontSize: '0.88rem', color: 'var(--text-muted)' }}>
            ⏳ Pending: <em>{pending.character_name}</em>
          </span>
          <Link to="/claim" style={{ ...linkBtn, fontSize: '0.82rem' }}>View</Link>
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

function HomePage() {
  const auth = useAuth()
  const navigate = useNavigate()
  const [search, setSearch] = useState('')

  function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    const name = search.trim()
    if (name) navigate(`/character/${encodeURIComponent(name)}`)
  }

  return (
    <main style={{ maxWidth: 640, margin: '4rem auto', padding: '0 1rem' }}>
      <h1 style={{ marginBottom: '0.25rem' }}>EQ2 TLE Companion</h1>
      <p style={{ color: 'var(--text-muted)', marginBottom: '2rem' }}>
        Character lookup for the Time-Locked Expansion server
      </p>

      <form onSubmit={handleSearch} style={{ display: 'flex', gap: '0.5rem', marginBottom: '2rem' }}>
        <input
          type="text"
          placeholder="Character name…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{ flex: 1 }}
        />
        <button type="submit" style={btnStyle('var(--accent)')}>
          Look up
        </button>
      </form>

      {auth.status === 'loading' && (
        <p style={{ color: 'var(--text-muted)' }}>Loading…</p>
      )}

      {auth.status === 'unauthenticated' && (
        <a href="/api/auth/login" style={btnStyle('#5865F2')}>
          Sign in with Discord
        </a>
      )}

      {auth.status === 'authenticated' && (
        <div>
          {/* User row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <img
              src={avatarUrl(auth.user)}
              alt="avatar"
              style={{ width: 40, height: 40, borderRadius: '50%' }}
            />
            <span>{auth.user.global_name ?? auth.user.username}</span>
            <button onClick={logout} style={{ ...btnStyle('var(--surface-raised)'), marginLeft: 'auto' }}>
              Sign out
            </button>
          </div>

          {/* Claim status */}
          <ClaimStrip />

          {/* Admin link */}
          {auth.user.is_admin && (
            <div style={{ marginTop: '1rem' }}>
              <Link to="/admin" style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                ⚙ Admin panel
              </Link>
            </div>
          )}
        </div>
      )}
    </main>
  )
}

// ── Styles ────────────────────────────────────────────────────────────────────

function btnStyle(bg: string): React.CSSProperties {
  return {
    display: 'inline-block',
    padding: '0.5rem 1.2rem',
    background: bg,
    color: 'var(--text)',
    borderRadius: 6,
    border: '1px solid var(--border)',
    cursor: 'pointer',
    fontSize: '0.95rem',
    whiteSpace: 'nowrap',
  }
}

const linkBtn: React.CSSProperties = {
  display: 'inline-block',
  padding: '0.35rem 0.9rem',
  background: 'var(--surface)',
  color: 'var(--text)',
  borderRadius: 6,
  border: '1px solid var(--border)',
  cursor: 'pointer',
  fontSize: '0.85rem',
  textDecoration: 'none',
  whiteSpace: 'nowrap',
}

export default HomePage
