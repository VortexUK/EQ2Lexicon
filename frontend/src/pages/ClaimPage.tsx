import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { avatarUrl, useAuth } from '../hooks/useAuth'
import { Claim, useClaim } from '../hooks/useClaim'

// ── Styles ────────────────────────────────────────────────────────────────────

const card: React.CSSProperties = {
  background: 'var(--surface)',
  border: '1px solid var(--border)',
  borderRadius: 8,
  padding: '1.25rem 1.5rem',
  marginTop: '1rem',
}

function btn(bg: string, fg = 'var(--text)'): React.CSSProperties {
  return {
    display: 'inline-block',
    padding: '0.4rem 1rem',
    background: bg,
    color: fg,
    borderRadius: 6,
    border: '1px solid var(--border)',
    cursor: 'pointer',
    fontSize: '0.88rem',
    whiteSpace: 'nowrap',
  }
}

// ── Claim form ────────────────────────────────────────────────────────────────

function ClaimForm({ onSubmitted, label = 'Request claim' }: {
  onSubmitted: () => void
  label?: string
}) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/claim', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ character_name: trimmed }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError(body.detail ?? `Error ${res.status}`)
      } else {
        setName('')
        onSubmitted()
      }
    } catch {
      setError('Network error — please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ marginTop: '0.75rem' }}>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <input
          type="text"
          placeholder="Character name…"
          value={name}
          onChange={e => setName(e.target.value)}
          disabled={busy}
          style={{ flex: 1 }}
        />
        <button type="submit" disabled={busy || !name.trim()} style={btn('var(--accent)')}>
          {busy ? 'Checking…' : label}
        </button>
      </div>
      {error && (
        <p style={{ color: '#f87171', fontSize: '0.85rem', marginTop: '0.4rem' }}>{error}</p>
      )}
    </form>
  )
}

// ── Approved character row ────────────────────────────────────────────────────

function ApprovedRow({ claim, onUpdate }: { claim: Claim; onUpdate: () => void }) {
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)

  async function handleSetPrimary() {
    setBusy(true)
    try {
      await fetch(`/api/claim/${claim.id}/set-primary`, { method: 'POST', credentials: 'include' })
      onUpdate()
    } finally {
      setBusy(false)
    }
  }

  async function handleRemove() {
    if (!window.confirm(`Remove ${claim.character_name} from your account?`)) return
    setBusy(true)
    try {
      await fetch(`/api/claim/${claim.id}`, { method: 'DELETE', credentials: 'include' })
      onUpdate()
    } finally {
      setBusy(false)
    }
  }

  const isPrimary = claim.is_primary === 1

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '0.6rem',
      padding: '0.55rem 0', borderBottom: '1px solid var(--border)',
    }}>
      {/* Primary / Alt badge */}
      <span style={{
        fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.05em',
        padding: '0.15rem 0.45rem', borderRadius: 4,
        background: isPrimary ? 'rgba(99,210,130,0.18)' : 'var(--surface-raised)',
        color: isPrimary ? '#4ade80' : 'var(--text-muted)',
        border: `1px solid ${isPrimary ? 'rgba(99,210,130,0.35)' : 'var(--border)'}`,
        flexShrink: 0,
        textTransform: 'uppercase',
      }}>
        {isPrimary ? 'Primary' : 'Alt'}
      </span>

      {/* Character name */}
      <button
        onClick={() => navigate(`/character/${encodeURIComponent(claim.character_name)}`)}
        style={{ ...btn('transparent'), border: 'none', padding: 0, color: 'var(--accent)', fontWeight: 600, fontSize: '0.95rem' }}
      >
        {claim.character_name}
      </button>

      {/* Actions */}
      <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
        {!isPrimary && (
          <button
            onClick={handleSetPrimary}
            disabled={busy}
            style={{ ...btn('var(--surface-raised)'), fontSize: '0.78rem', padding: '0.2rem 0.55rem' }}
            title="Set as primary character"
          >
            {busy ? '…' : 'Set Primary'}
          </button>
        )}
        <button
          onClick={handleRemove}
          disabled={busy}
          style={{ ...btn('transparent'), border: 'none', color: 'var(--text-muted)', fontSize: '0.82rem', padding: '0.2rem 0.4rem' }}
          title="Remove this character"
        >
          {busy ? '…' : 'Remove'}
        </button>
      </div>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ClaimPage() {
  const auth = useAuth()
  const claimState = useClaim()
  const [cancelBusy, setCancelBusy] = useState(false)
  const [showChangeForm, setShowChangeForm] = useState(false)

  async function handleCancelPending(claimId: number) {
    setCancelBusy(true)
    try {
      await fetch(`/api/claim/${claimId}`, { method: 'DELETE', credentials: 'include' })
      claimState.refetch()
    } finally {
      setCancelBusy(false)
    }
  }

  const isUnauth = auth.status === 'unauthenticated' || claimState.status === 'unauthenticated'
  const isLoading = auth.status === 'loading' || claimState.status === 'loading'

  return (
    <main style={{ maxWidth: 560, margin: '3rem auto', padding: '0 1rem' }}>
      <Link to="/" style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>← Back</Link>
      <h1 style={{ margin: '0.75rem 0 0.25rem' }}>My Characters</h1>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginBottom: '1.5rem' }}>
        Link your Discord account to your EQ2 characters on {import.meta.env.VITE_EQ2_WORLD ?? 'Varsoon'}.
        Each claim is reviewed by a guild officer before being approved.
      </p>

      {isUnauth && (
        <div style={card}>
          <p style={{ marginBottom: '1rem' }}>You need to sign in with Discord first.</p>
          <a href="/api/auth/login" style={btn('#5865F2', '#fff')}>Sign in with Discord</a>
        </div>
      )}

      {isLoading && <p style={{ color: 'var(--text-muted)' }}>Loading…</p>}

      {claimState.status === 'error' && (
        <p style={{ color: '#f87171' }}>Failed to load. Try refreshing.</p>
      )}

      {auth.status === 'authenticated' && claimState.status === 'ready' && (() => {
        const { approved, pending } = claimState.data
        const hasAny = approved.length > 0 || pending !== null

        return (
          <>
            {/* Approved characters */}
            {approved.length > 0 && (
              <div style={card}>
                <div style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
                  Approved Characters
                </div>
                {approved.map(c => (
                  <ApprovedRow key={c.id} claim={c} onUpdate={claimState.refetch} />
                ))}
              </div>
            )}

            {/* Pending claim */}
            {pending && (
              <div style={{ ...card, borderColor: 'rgba(234,179,8,0.4)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <span style={{ fontSize: '1.2rem' }}>⏳</span>
                  <div>
                    <div style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)' }}>
                      Pending approval
                    </div>
                    <div style={{ color: 'var(--accent)', fontWeight: 600 }}>{pending.character_name}</div>
                  </div>
                  <button
                    onClick={() => handleCancelPending(pending.id)}
                    disabled={cancelBusy}
                    style={{ ...btn('var(--surface-raised)'), marginLeft: 'auto', fontSize: '0.82rem' }}
                  >
                    {cancelBusy ? 'Cancelling…' : 'Cancel'}
                  </button>
                </div>
              </div>
            )}

            {/* Add another character */}
            <div style={card}>
              {!hasAny ? (
                <>
                  <div style={{ fontWeight: 600, marginBottom: '0.4rem' }}>Claim your character</div>
                  <p style={{ color: 'var(--text-muted)', fontSize: '0.88rem', marginBottom: 0 }}>
                    Enter your character's name exactly as it appears in-game.
                  </p>
                  <ClaimForm onSubmitted={claimState.refetch} />
                </>
              ) : (
                <>
                  <button
                    onClick={() => setShowChangeForm(v => !v)}
                    style={{ ...btn('transparent'), border: 'none', padding: 0, color: 'var(--text-muted)', fontSize: '0.88rem' }}
                  >
                    {showChangeForm ? '▾ Hide' : '＋ Add another character'}
                  </button>
                  {showChangeForm && (
                    <div style={{ marginTop: '0.5rem' }}>
                      {pending && (
                        <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginBottom: 0 }}>
                          This will replace your current pending claim.
                        </p>
                      )}
                      <ClaimForm
                        label="Request claim"
                        onSubmitted={() => { claimState.refetch(); setShowChangeForm(false) }}
                      />
                    </div>
                  )}
                </>
              )}
            </div>
          </>
        )
      })()}

      {/* Footer */}
      {auth.status === 'authenticated' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '2rem', color: 'var(--text-muted)', fontSize: '0.82rem' }}>
          <img src={avatarUrl(auth.user)} alt="" style={{ width: 24, height: 24, borderRadius: '50%' }} />
          <span>Signed in as <strong>{auth.user.global_name ?? auth.user.username}</strong></span>
        </div>
      )}
    </main>
  )
}
