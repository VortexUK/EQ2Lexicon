import React, { useCallback, useState } from 'react'

import Breadcrumb from '../components/Breadcrumb'
import { Button, Card } from '../components/ui'
import { fmtLocalDateTime } from '../formatters'
import { useFetch } from '../hooks/useFetch'

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

// ── Timing constants ─────────────────────────────────────────────────────────

const COPY_FEEDBACK_DURATION_MS = 1500

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTs(unix: number | null): string {
  return unix ? fmtLocalDateTime(unix) : '—'
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface TokensResponse {
  tokens: TokenRow[]
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function TokensPage() {
  const { data: fetched, loading, error, refetch } = useFetch<TokensResponse>('/api/auth/tokens')
  const tokens = fetched?.tokens ?? null

  // Modal state for minting + showing the new raw token once
  const [mintOpen, setMintOpen] = useState(false)
  const [mintName, setMintName] = useState('')
  const [minting, setMinting] = useState(false)
  const [mintedToken, setMintedToken] = useState<string | null>(null)
  const [copyState, setCopyState] = useState<'idle' | 'copied'>('idle')

  const [mintError, setMintError] = useState<string | null>(null)

  const onMint = useCallback(async () => {
    const name = mintName.trim()
    if (!name) return
    setMinting(true)
    setMintError(null)
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
      refetch()
    } catch (err) {
      setMintError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setMinting(false)
    }
  }, [mintName, refetch])

  const onRevoke = useCallback(async (id: number, name: string) => {
    if (!confirm(`Revoke token "${name}"? Any plugin using it will stop working immediately.`)) return
    try {
      const r = await fetch(`/api/auth/tokens/${id}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!r.ok && r.status !== 204) throw new Error(`Server error ${r.status}`)
      refetch()
    } catch (err) {
      setMintError(err instanceof Error ? err.message : 'Unknown error')
    }
  }, [refetch])

  const onCopy = useCallback(async () => {
    if (!mintedToken) return
    try {
      await navigator.clipboard.writeText(mintedToken)
      setCopyState('copied')
      setTimeout(() => setCopyState('idle'), COPY_FEEDBACK_DURATION_MS)
    } catch {
      // older browsers — leave it visible for manual copy
    }
  }, [mintedToken])

  const closeMint = useCallback(() => {
    setMintOpen(false)
    setMintedToken(null)
    setMintName('')
    setMintError(null)
  }, [])

  return (
    <main className="max-w-[900px] mx-auto px-4 py-6">
      <Breadcrumb items={[{ label: 'Settings', to: '/settings/tokens' }, { label: 'API Tokens' }]} />

      <div className="flex items-baseline justify-between mb-4">
        <h1 className="font-heading text-[1.7rem] text-gold m-0">
          API Tokens
        </h1>
        <Button variant="primary" onClick={() => setMintOpen(true)}>
          + New token
        </Button>
      </div>

      <p className="text-text-muted text-[0.88rem] mt-0 mb-[1.4rem]">
        Tokens authenticate external integrations (the ACT plugin) to upload parses on your behalf.
        Treat them like passwords — anyone with the raw token can post parses under your account.
      </p>

      {loading && !tokens && <p className="text-text-muted">Loading…</p>}
      {error && <p className="text-danger">{error}</p>}
      {mintError && <p className="text-danger">{mintError}</p>}

      {!loading && tokens && tokens.length === 0 && (
        <p className="text-text-muted">
          You haven't generated any tokens yet. Click <em>+ New token</em> to create one for your ACT plugin.
        </p>
      )}

      {tokens && tokens.length > 0 && (
        <Card className="p-0 overflow-hidden">
          <div className={tableHeaderRow}>
            <div>Name</div>
            <div>Token</div>
            <div>Created</div>
            <div>Last used</div>
            <div></div>
          </div>
          {tokens.map(t => (
            <div key={t.id} className={tableRow} style={{ opacity: t.revoked_at ? 0.55 : 1 }}>
              <div>{t.name}</div>
              <div className="font-mono text-[0.82rem] text-text-muted">
                {t.token_prefix}…
              </div>
              <div className="text-text-muted text-[0.82rem]">{fmtTs(t.created_at)}</div>
              <div className="text-text-muted text-[0.82rem]">
                {t.revoked_at ? <span className="text-danger">revoked {fmtTs(t.revoked_at)}</span> : fmtTs(t.last_used_at)}
              </div>
              <div className="text-right">
                {!t.revoked_at && (
                  <Button variant="danger" size="sm" onClick={() => onRevoke(t.id, t.name)}>
                    Revoke
                  </Button>
                )}
              </div>
            </div>
          ))}
        </Card>
      )}

      {mintOpen && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-modal p-4" onClick={closeMint}>
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
        </div>
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
    <div style={modalContent} onClick={e => e.stopPropagation()}>
        {mintedToken ? (
          <>
            <h2 className="font-heading text-gold text-[1.25rem] mb-2 mt-0">Token created</h2>
            <p className="text-text-muted text-[0.85rem]">
              Copy this token now and paste it into your ACT plugin. You won't be able to see it again —
              if you lose it, revoke it and create a new one.
            </p>
            <div
              className="border border-gold rounded-md py-[0.7rem] px-[0.9rem] font-mono text-[0.88rem] break-all text-gold my-4 select-all"
              style={{ background: 'rgba(0,0,0,0.4)' }}
            >
              {mintedToken}
            </div>
            <div className="flex gap-2 justify-end">
              <Button variant="primary" onClick={onCopy}>
                {copyState === 'copied' ? 'Copied ✓' : 'Copy token'}
              </Button>
              <Button variant="secondary" onClick={onClose}>
                Done
              </Button>
            </div>
          </>
        ) : (
          <>
            <h2 className="font-heading text-gold text-[1.25rem] mb-2 mt-0">New API token</h2>
            <p className="text-text-muted text-[0.85rem]">
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
              className="w-full bg-surface border border-border rounded-md text-text text-[0.92rem] py-2 px-[0.7rem] mt-2.5 mb-4 box-border"
            />
            <div className="flex gap-2 justify-end">
              <Button variant="secondary" onClick={onClose}>Cancel</Button>
              <Button variant="primary" onClick={onMint} disabled={!name.trim() || minting}>
                {minting ? 'Generating…' : 'Generate token'}
              </Button>
            </div>
          </>
        )}
      </div>
  )
}

// ── Styles ───────────────────────────────────────────────────────────────────

const tableHeaderRow =
  'grid grid-cols-[minmax(120px,1.4fr)_minmax(120px,1fr)_140px_160px_90px] gap-x-[0.6rem] px-[0.85rem] py-[0.55rem] border-b border-border text-[0.72rem] uppercase tracking-[0.06em] text-text-muted'

const tableRow =
  'grid grid-cols-[minmax(120px,1.4fr)_minmax(120px,1fr)_140px_160px_90px] gap-x-[0.6rem] px-[0.85rem] py-[0.55rem] border-b border-border items-center text-[0.88rem]'

const modalContent: React.CSSProperties = {
  background: '#1a1d27',
  border: '1px solid var(--border)',
  borderRadius: 8,
  padding: '1.5rem',
  maxWidth: 520,
  width: '100%',
  boxShadow: '0 8px 32px rgba(0, 0, 0, 0.6)',
}
