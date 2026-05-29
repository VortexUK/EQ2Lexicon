import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { Claim, useClaim } from '../hooks/useClaim'
import { Button, Card, SectionLabel } from '../components/ui'
import { DiscordButton } from '../components/ui/DiscordButton'

// ── Styles ────────────────────────────────────────────────────────────────────

// Shared Card layout: matches `padding: 1.25rem 1.5rem; margin-top: 1rem`.
const CARD_CLS = 'px-6 py-5 mt-4'

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
    <form onSubmit={handleSubmit} className="mt-3">
      <div className="flex gap-2">
        <input
          type="text"
          placeholder="Character name…"
          value={name}
          onChange={e => setName(e.target.value)}
          disabled={busy}
          className="flex-1"
        />
        <Button type="submit" variant="primary" disabled={busy || !name.trim()}>
          {busy ? 'Checking…' : label}
        </Button>
      </div>
      {error && (
        <p className="text-danger text-[0.85rem] mt-[0.4rem]">{error}</p>
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
    <div className="flex items-center gap-[0.6rem] py-[0.55rem] border-b border-border">
      {/* Primary / Alt badge */}
      <span
        className="text-[0.68rem] font-bold tracking-[0.05em] py-[0.15rem] px-[0.45rem] rounded-sm border shrink-0 uppercase"
        style={{
          background: isPrimary ? 'rgba(var(--accent-rgb), 0.18)' : 'var(--surface-raised)',
          color: isPrimary ? '#4ade80' : 'var(--text-muted)',
          borderColor: isPrimary ? 'rgba(var(--accent-rgb), 0.35)' : 'var(--border)',
        }}
      >
        {isPrimary ? 'Primary' : 'Alt'}
      </span>

      {/* Character name */}
      <Button
        variant="ghost"
        onClick={() => navigate(`/character/${encodeURIComponent(claim.character_name)}`)}
        className="p-0 text-gold font-semibold text-[0.95rem]"
      >
        {claim.character_name}
      </Button>

      {/* Actions */}
      <div className="ml-auto flex gap-[0.4rem] items-center">
        {!isPrimary && (
          <Button
            variant="secondary"
            size="sm"
            onClick={handleSetPrimary}
            disabled={busy}
            title="Set as primary character"
          >
            {busy ? '…' : 'Set Primary'}
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={handleRemove}
          disabled={busy}
          title="Remove this character"
        >
          {busy ? '…' : 'Remove'}
        </Button>
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
    <main className="max-w-[560px] mx-auto my-12 px-4">
      <h1 className="mt-3 mb-2">My Characters</h1>
      <Card className="border-l-[3px] border-l-gold/50 py-[0.9rem] px-[1.1rem] mb-6 text-[0.88rem] text-text-muted leading-[1.65]">
        <p className="mt-0 mx-0 mb-2 text-text font-semibold">
          What is character claiming?
        </p>
        <p className="mt-0 mx-0 mb-2">
          Linking your Discord account to your in-game characters unlocks
          personalised features — your character sheet, spell upgrade tracker,
          and gear overview are all tied to your claim.
        </p>
        <p className="mt-0 mx-0 mb-2">
          After submitting a claim, a <strong className="text-text">guild officer or admin</strong> will
          verify that the character belongs to you and approve the request.
          You'll be notified once it's approved.
        </p>
        <p className="m-0">
          You can have multiple characters linked — mark one as your{' '}
          <strong className="text-text">primary</strong> to set it as
          your default on the home page.
        </p>
      </Card>

      {isUnauth && (
        <Card className={CARD_CLS}>
          <p className="mb-4">You need to sign in with Discord first.</p>
          <DiscordButton />
        </Card>
      )}

      {isLoading && <p className="text-text-muted">Loading…</p>}

      {claimState.status === 'error' && (
        <p className="text-danger">Failed to load. Try refreshing.</p>
      )}

      {auth.status === 'authenticated' && claimState.status === 'ready' && (() => {
        const { pending } = claimState.data
        const approved = [...claimState.data.approved].sort((a, b) => b.is_primary - a.is_primary)
        const hasAny = approved.length > 0 || pending !== null

        return (
          <>
            {/* Approved characters */}
            {approved.length > 0 && (
              <Card className={CARD_CLS}>
                <SectionLabel variant="muted">Approved Characters</SectionLabel>
                {approved.map(c => (
                  <ApprovedRow key={c.id} claim={c} onUpdate={claimState.refetch} />
                ))}
              </Card>
            )}

            {/* Pending claim */}
            {pending && (
              <Card className={CARD_CLS} style={{ borderColor: 'rgba(var(--gold-rgb), 0.5)' }}>
                <div className="flex items-center gap-2">
                  <span className="text-[1.2rem]">⏳</span>
                  <div>
                    <div className="text-[0.72rem] uppercase tracking-[0.06em] text-text-muted">
                      Pending approval
                    </div>
                    <div className="text-gold font-semibold">{pending.character_name}</div>
                  </div>
                  <Button
                    variant="secondary"
                    onClick={() => handleCancelPending(pending.id)}
                    disabled={cancelBusy}
                    className="ml-auto"
                  >
                    {cancelBusy ? 'Cancelling…' : 'Cancel'}
                  </Button>
                </div>
              </Card>
            )}

            {/* Add another character */}
            <Card className={CARD_CLS}>
              {!hasAny ? (
                <>
                  <div className="font-semibold mb-[0.4rem]">Claim your character</div>
                  <p className="text-text-muted text-[0.88rem] mb-0">
                    Enter your character's name exactly as it appears in-game.
                  </p>
                  <ClaimForm onSubmitted={claimState.refetch} />
                </>
              ) : (
                <>
                  <Button
                    variant="ghost"
                    onClick={() => setShowChangeForm(v => !v)}
                    className="p-0 text-[0.88rem]"
                  >
                    {showChangeForm ? '▾ Hide' : '＋ Add another character'}
                  </Button>
                  {showChangeForm && (
                    <div className="mt-2">
                      {pending && (
                        <p className="text-text-muted text-[0.85rem] mb-0">
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
            </Card>
          </>
        )
      })()}

    </main>
  )
}
