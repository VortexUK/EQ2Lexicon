import type { CSSProperties } from 'react'
import { useCallback, useEffect, useState } from 'react'

import Breadcrumb from '../components/Breadcrumb'

// ── Types ─────────────────────────────────────────────────────────────────────

interface TokenRow {
  id: number
  name: string
  token_prefix: string
  created_at: number
  last_used_at: number | null
  revoked_at: number | null
}

interface MintResponse {
  token: string
  row: TokenRow
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTs(unix: number | null): string {
  if (!unix) return '—'
  return new Date(unix * 1000).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function TokensPage() {
  const [tokens, setTokens] = useState<TokenRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // Modal state for minting + showing the new raw token once
  const [mintOpen, setMintOpen] = useState(false)
  const [mintName, setMintName] = useState('')
  const [minting, setMinting] = useState(false)
  const [mintedToken, setMintedToken] = useState<string | null>(null)
  const [copyState, setCopyState] = useState<'idle' | 'copied'>('idle')

  const fetchTokens = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch('/api/auth/tokens', { credentials: 'include' })
      if (!r.ok) throw new Error(`Server error ${r.status}`)
      const data = await r.json()
      setTokens(data.tokens)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchTokens() }, [fetchTokens])

  const onMint = useCallback(async () => {
    const name = mintName.trim()
    if (!name) return
    setMinting(true)
    setError(null)
    try {
      const r = await fetch('/api/auth/tokens', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      if (!r.ok) throw new Error(`Server error ${r.status}`)
      const data: MintResponse = await r.json()
      setMintedToken(data.token)
      setMintName('')
      setTokens(prev => prev ? [data.row, ...prev] : [data.row])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setMinting(false)
    }
  }, [mintName])

  const onRevoke = useCallback(async (id: number, name: string) => {
    if (!confirm(`Revoke token "${name}"? Any plugin using it will stop working immediately.`)) return
    try {
      const r = await fetch(`/api/auth/tokens/${id}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!r.ok && r.status !== 204) throw new Error(`Server error ${r.status}`)
      // Mark locally as revoked (server-side gives no body) for instant UI feedback.
      setTokens(prev => prev
        ? prev.map(t => t.id === id ? { ...t, revoked_at: Math.floor(Date.now() / 1000) } : t)
        : prev,
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    }
  }, [])

  const onCopy = useCallback(async () => {
    if (!mintedToken) return
    try {
      await navigator.clipboard.writeText(mintedToken)
      setCopyState('copied')
      setTimeout(() => setCopyState('idle'), 1500)
    } catch {
      // older browsers — leave it visible for manual copy
    }
  }, [mintedToken])

  const closeMint = useCallback(() => {
    setMintOpen(false)
    setMintedToken(null)
    setMintName('')
    setError(null)
  }, [])

  return (
    <main style={pageStyle}>
      <Breadcrumb items={[{ label: 'Settings', to: '/settings/tokens' }, { label: 'API Tokens' }]} />

      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: '1rem' }}>
        <h1 style={{ fontFamily: 'var(--font-heading)', fontSize: '1.7rem', color: 'var(--gold)', margin: 0 }}>
          API Tokens
        </h1>
        <button onClick={() => setMintOpen(true)} style={btnPrimary}>
          + New token
        </button>
      </div>

      <p style={{ color: 'var(--text-muted)', fontSize: '0.88rem', marginTop: 0, marginBottom: '1.4rem' }}>
        Tokens authenticate external integrations (the ACT plugin) to upload parses on your behalf.
        Treat them like passwords — anyone with the raw token can post parses under your account.
      </p>

      {loading && <p style={{ color: 'var(--text-muted)' }}>Loading…</p>}
      {error && <p style={{ color: 'var(--danger)' }}>{error}</p>}

      {!loading && tokens && tokens.length === 0 && (
        <p style={{ color: 'var(--text-muted)' }}>
          You haven't generated any tokens yet. Click <em>+ New token</em> to create one for your ACT plugin.
        </p>
      )}

      {!loading && tokens && tokens.length > 0 && (
        <div style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 8,
          overflow: 'hidden',
        }}>
          <div style={tableHeaderRow}>
            <div>Name</div>
            <div>Token</div>
            <div>Created</div>
            <div>Last used</div>
            <div></div>
          </div>
          {tokens.map(t => (
            <div key={t.id} style={{
              ...tableRow,
              opacity: t.revoked_at ? 0.55 : 1,
            }}>
              <div>{t.name}</div>
              <div style={{ fontFamily: 'monospace', fontSize: '0.82rem', color: 'var(--text-muted)' }}>
                {t.token_prefix}…
              </div>
              <div style={{ color: 'var(--text-muted)', fontSize: '0.82rem' }}>{fmtTs(t.created_at)}</div>
              <div style={{ color: 'var(--text-muted)', fontSize: '0.82rem' }}>
                {t.revoked_at ? <span style={{ color: 'var(--danger)' }}>revoked {fmtTs(t.revoked_at)}</span> : fmtTs(t.last_used_at)}
              </div>
              <div style={{ textAlign: 'right' }}>
                {!t.revoked_at && (
                  <button onClick={() => onRevoke(t.id, t.name)} style={btnDanger}>
                    Revoke
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {mintOpen && (
        <MintModal
          name={mintName}
          setName={setMintName}
          mintedToken={mintedToken}
          minting={minting}
          onMint={onMint}
          onClose={closeMint}
          onCopy={onCopy}
          copyState={copyState}
        />
      )}
    </main>
  )
}

// ── Mint modal ───────────────────────────────────────────────────────────────

function MintModal({
  name, setName, mintedToken, minting, onMint, onClose, onCopy, copyState,
}: {
  name: string
  setName: (v: string) => void
  mintedToken: string | null
  minting: boolean
  onMint: () => void
  onClose: () => void
  onCopy: () => void
  copyState: 'idle' | 'copied'
}) {
  return (
    <div style={modalOverlay} onClick={onClose}>
      <div style={modalContent} onClick={e => e.stopPropagation()}>
        {mintedToken ? (
          <>
            <h2 style={modalTitle}>Token created</h2>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
              Copy this token now and paste it into your ACT plugin. You won't be able to see it again —
              if you lose it, revoke it and create a new one.
            </p>
            <div style={{
              background: 'rgba(0,0,0,0.4)',
              border: '1px solid var(--gold)',
              borderRadius: 6,
              padding: '0.7rem 0.9rem',
              fontFamily: 'monospace',
              fontSize: '0.88rem',
              wordBreak: 'break-all',
              color: 'var(--gold)',
              margin: '1rem 0',
              userSelect: 'all',
            }}>
              {mintedToken}
            </div>
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <button onClick={onCopy} style={btnPrimary}>
                {copyState === 'copied' ? 'Copied ✓' : 'Copy token'}
              </button>
              <button onClick={onClose} style={btnSecondary}>
                Done
              </button>
            </div>
          </>
        ) : (
          <>
            <h2 style={modalTitle}>New API token</h2>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
              Give the token a name so you can recognise it later (e.g. "Desktop ACT" or "Laptop").
            </p>
            <input
              autoFocus
              type="text"
              placeholder="Token name"
              value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && name.trim() && !minting) onMint() }}
              maxLength={64}
              style={{
                width: '100%',
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 6,
                color: 'var(--text)',
                fontSize: '0.92rem',
                padding: '0.5rem 0.7rem',
                margin: '0.6rem 0 1rem',
                boxSizing: 'border-box',
              }}
            />
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <button onClick={onClose} style={btnSecondary}>Cancel</button>
              <button
                onClick={onMint}
                disabled={!name.trim() || minting}
                style={{ ...btnPrimary, opacity: !name.trim() || minting ? 0.5 : 1 }}
              >
                {minting ? 'Generating…' : 'Generate token'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Styles ───────────────────────────────────────────────────────────────────

const pageStyle: CSSProperties = { maxWidth: 900, margin: '0 auto', padding: '1.5rem 1rem' }

const tableHeaderRow: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'minmax(120px, 1.4fr) minmax(120px, 1fr) 140px 160px 90px',
  gap: '0.6rem',
  padding: '0.55rem 0.85rem',
  borderBottom: '1px solid var(--border)',
  fontSize: '0.72rem',
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
  color: 'var(--text-muted)',
}

const tableRow: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'minmax(120px, 1.4fr) minmax(120px, 1fr) 140px 160px 90px',
  gap: '0.6rem',
  padding: '0.55rem 0.85rem',
  borderBottom: '1px solid var(--border)',
  alignItems: 'center',
  fontSize: '0.88rem',
}

const btnPrimary: CSSProperties = {
  background: 'var(--gold)',
  color: '#0f1117',
  border: 'none',
  borderRadius: 6,
  fontWeight: 700,
  fontSize: '0.85rem',
  padding: '0.4rem 0.95rem',
  cursor: 'pointer',
}

const btnSecondary: CSSProperties = {
  background: 'var(--surface)',
  color: 'var(--text)',
  border: '1px solid var(--border)',
  borderRadius: 6,
  fontSize: '0.85rem',
  padding: '0.4rem 0.95rem',
  cursor: 'pointer',
}

const btnDanger: CSSProperties = {
  background: 'none',
  color: 'var(--danger)',
  border: '1px solid var(--border)',
  borderRadius: 4,
  fontSize: '0.78rem',
  padding: '0.25rem 0.65rem',
  cursor: 'pointer',
}

const modalOverlay: CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(0, 0, 0, 0.7)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 1000,
  padding: '1rem',
}

const modalContent: CSSProperties = {
  background: '#1a1d27',
  border: '1px solid var(--border)',
  borderRadius: 8,
  padding: '1.5rem',
  maxWidth: 520,
  width: '100%',
  boxShadow: '0 8px 32px rgba(0, 0, 0, 0.6)',
}

const modalTitle: CSSProperties = {
  fontFamily: 'var(--font-heading)',
  color: 'var(--gold)',
  margin: '0 0 0.5rem',
  fontSize: '1.25rem',
}
