import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ClaimDetail {
  id: number
  discord_id: string
  discord_name: string
  discord_username: string | null
  avatar: string | null
  character_name: string
  status: string
  requested_at: number
  reviewed_at: number | null
  reviewed_by: string | null
  note: string | null
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function discordDisplayName(name: string, username: string | null): string {
  if (!username || username === name) return name
  return `${name} (${username})`
}

function discordAvatar(id: string, avatar: string | null): string {
  if (avatar) return `https://cdn.discordapp.com/avatars/${id}/${avatar}.png?size=64`
  const index = Number(BigInt(id) >> 22n) % 6
  return `https://cdn.discordapp.com/embed/avatars/${index}.png`
}

function relativeTime(unix: number): string {
  const diff = Math.floor(Date.now() / 1000) - unix
  if (diff < 60)   return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function groupByUser(claims: ClaimDetail[]): { discordId: string; claims: ClaimDetail[] }[] {
  const map = new Map<string, ClaimDetail[]>()
  for (const c of claims) {
    const bucket = map.get(c.discord_id) ?? []
    bucket.push(c)
    map.set(c.discord_id, bucket)
  }
  return Array.from(map.entries()).map(([discordId, claims]) => ({ discordId, claims }))
}

const STATUS_BADGE: Record<string, React.CSSProperties> = {
  pending:    { background: 'rgba(234,179,8,0.2)',    color: '#fbbf24', border: '1px solid rgba(234,179,8,0.4)'    },
  approved:   { background: 'rgba(34,197,94,0.15)',   color: '#4ade80', border: '1px solid rgba(34,197,94,0.4)'    },
  rejected:   { background: 'rgba(239,68,68,0.15)',   color: '#f87171', border: '1px solid rgba(239,68,68,0.4)'    },
  withdrawn:  { background: 'rgba(100,116,139,0.15)', color: '#94a3b8', border: '1px solid rgba(100,116,139,0.3)'  },
  superseded: { background: 'rgba(100,116,139,0.15)', color: '#94a3b8', border: '1px solid rgba(100,116,139,0.3)'  },
}

function StatusBadge({ status }: { status: string }) {
  const style = STATUS_BADGE[status] ?? STATUS_BADGE.withdrawn
  return (
    <span style={{ ...style, borderRadius: 4, padding: '1px 7px', fontSize: '0.75rem', fontWeight: 600 }}>
      {status}
    </span>
  )
}

// ── Pending claim row (inside a user section) ─────────────────────────────────

function PendingClaimRow({ claim, onAction }: { claim: ClaimDetail; onAction: () => void }) {
  const [rejectOpen, setRejectOpen] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  async function doAction(url: string, body?: object | null, method = 'POST') {
    setBusy(true)
    try {
      await fetch(url, {
        method,
        credentials: 'include',
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      })
      onAction()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{
      padding: '0.55rem 0.75rem',
      borderBottom: '1px solid var(--border)',
      background: 'var(--surface-raised)',
      borderRadius: 4,
      marginBottom: '0.35rem',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
        <span style={{ color: 'var(--accent)', fontWeight: 600, fontSize: '0.92rem' }}>
          {claim.character_name}
        </span>
        <StatusBadge status={claim.status} />
        <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginLeft: 'auto' }}>
          {relativeTime(claim.requested_at)}
        </span>
      </div>

      {!rejectOpen ? (
        <div style={{ display: 'flex', gap: '0.45rem', marginTop: '0.45rem', alignItems: 'center' }}>
          <button
            onClick={() => doAction(`/api/admin/claims/${claim.id}/approve`)}
            disabled={busy}
            style={{
              padding: '0.25rem 0.75rem', borderRadius: 5, cursor: 'pointer', fontSize: '0.82rem',
              background: 'rgba(34,197,94,0.15)', color: '#4ade80',
              border: '1px solid rgba(34,197,94,0.4)',
            }}
          >
            Approve
          </button>
          <button
            onClick={() => setRejectOpen(true)}
            disabled={busy}
            style={{
              padding: '0.25rem 0.75rem', borderRadius: 5, cursor: 'pointer', fontSize: '0.82rem',
              background: 'rgba(239,68,68,0.1)', color: '#f87171',
              border: '1px solid rgba(239,68,68,0.3)',
            }}
          >
            Reject
          </button>
          <button
            onClick={() => doAction(`/api/admin/claims/${claim.id}`, null, 'DELETE')}
            disabled={busy}
            style={{
              marginLeft: 'auto', background: 'transparent', border: 'none',
              cursor: 'pointer', color: 'var(--text-muted)', fontSize: '0.85rem', padding: '0 0.2rem',
            }}
            title="Delete this claim permanently"
          >
            🗑
          </button>
        </div>
      ) : (
        <div style={{ marginTop: '0.45rem' }}>
          <textarea
            placeholder="Optional reason for rejection…"
            value={note}
            onChange={e => setNote(e.target.value)}
            rows={2}
            style={{ width: '100%', boxSizing: 'border-box', fontSize: '0.82rem', resize: 'vertical' }}
          />
          <div style={{ display: 'flex', gap: '0.45rem', marginTop: '0.35rem' }}>
            <button
              onClick={() => doAction(`/api/admin/claims/${claim.id}/reject`, { note: note || null })}
              disabled={busy}
              style={{
                padding: '0.25rem 0.75rem', borderRadius: 5, cursor: 'pointer', fontSize: '0.82rem',
                background: 'rgba(239,68,68,0.15)', color: '#f87171',
                border: '1px solid rgba(239,68,68,0.4)',
              }}
            >
              {busy ? 'Rejecting…' : 'Confirm reject'}
            </button>
            <button
              onClick={() => { setRejectOpen(false); setNote('') }}
              style={{
                padding: '0.25rem 0.75rem', borderRadius: 5, cursor: 'pointer', fontSize: '0.82rem',
                background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-muted)',
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── History claim row (inside a user section) ─────────────────────────────────

function HistoryClaimRow({ claim, onDelete }: { claim: ClaimDetail; onDelete: () => void }) {
  async function handleDelete() {
    await fetch(`/api/admin/claims/${claim.id}`, { method: 'DELETE', credentials: 'include' })
    onDelete()
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: '0.6rem',
      padding: '0.45rem 0.75rem', borderBottom: '1px solid var(--border)', flexWrap: 'wrap',
    }}>
      <span style={{ color: 'var(--accent)', fontWeight: 500, fontSize: '0.9rem' }}>
        {claim.character_name}
      </span>
      <StatusBadge status={claim.status} />
      {claim.note && (
        <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem', fontStyle: 'italic' }}>
          "{claim.note}"
        </span>
      )}
      <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginLeft: 'auto' }}>
        {relativeTime(claim.requested_at)}
      </span>
      <button
        onClick={handleDelete}
        style={{
          background: 'transparent', border: 'none', cursor: 'pointer',
          color: 'var(--text-muted)', fontSize: '0.85rem', padding: '0 0.2rem',
        }}
        title="Delete this claim permanently"
      >
        🗑
      </button>
    </div>
  )
}

// ── User section (collapsible) ────────────────────────────────────────────────

function UserSection({
  discordId,
  claims,
  mode,
  defaultOpen,
  onAction,
}: {
  discordId: string
  claims: ClaimDetail[]
  mode: 'pending' | 'history'
  defaultOpen: boolean
  onAction: () => void
}) {
  const [open, setOpen] = useState(defaultOpen)
  const [confirmAll, setConfirmAll] = useState(false)
  const [deleting, setDeleting] = useState(false)

  // Representative claim for user meta (they all share the same user)
  const rep = claims[0]
  const displayName = discordDisplayName(rep.discord_name, rep.discord_username)

  async function handleDeleteAll() {
    setDeleting(true)
    try {
      await fetch(`/api/admin/users/${discordId}/claims`, {
        method: 'DELETE',
        credentials: 'include',
      })
      onAction()
    } finally {
      setDeleting(false)
      setConfirmAll(false)
    }
  }

  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 8,
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '0.65rem',
        padding: '0.65rem 0.9rem',
        background: open ? 'var(--surface-raised)' : 'var(--surface)',
        borderBottom: open ? '1px solid var(--border)' : 'none',
        flexWrap: 'wrap',
      }}>
        {/* Expand toggle + avatar + name */}
        <button
          onClick={() => { setOpen(v => !v); setConfirmAll(false) }}
          style={{
            background: 'transparent', border: 'none', cursor: 'pointer',
            color: 'var(--text-muted)', fontSize: '0.8rem', padding: '0 0.1rem', flexShrink: 0,
          }}
          aria-label={open ? 'Collapse' : 'Expand'}
        >
          {open ? '▾' : '▸'}
        </button>
        <img
          src={discordAvatar(discordId, rep.avatar)}
          alt=""
          style={{ width: 30, height: 30, borderRadius: '50%', flexShrink: 0 }}
        />
        <span style={{ fontWeight: 600, fontSize: '0.92rem' }}>{displayName}</span>
        <span style={{
          fontSize: '0.72rem', padding: '1px 7px', borderRadius: 10,
          background: 'var(--surface-raised)', border: '1px solid var(--border)',
          color: 'var(--text-muted)', flexShrink: 0,
        }}>
          {claims.length} {claims.length === 1 ? 'claim' : 'claims'}
        </span>

        {/* Delete all — normal button or inline confirm */}
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {!confirmAll ? (
            <button
              onClick={() => setConfirmAll(true)}
              style={{
                fontSize: '0.78rem', padding: '0.2rem 0.6rem', borderRadius: 5, cursor: 'pointer',
                background: 'rgba(239,68,68,0.08)', color: '#f87171',
                border: '1px solid rgba(239,68,68,0.3)',
              }}
              title="Delete all claims for this user"
            >
              Delete all
            </button>
          ) : (
            <>
              <span style={{ fontSize: '0.78rem', color: '#f87171' }}>
                Delete all {claims.length} claims? This is permanent.
              </span>
              <button
                onClick={handleDeleteAll}
                disabled={deleting}
                style={{
                  fontSize: '0.78rem', padding: '0.2rem 0.65rem', borderRadius: 5, cursor: 'pointer',
                  background: 'rgba(239,68,68,0.2)', color: '#f87171',
                  border: '1px solid rgba(239,68,68,0.5)', fontWeight: 600,
                }}
              >
                {deleting ? '…' : 'Confirm'}
              </button>
              <button
                onClick={() => setConfirmAll(false)}
                style={{
                  fontSize: '0.78rem', padding: '0.2rem 0.55rem', borderRadius: 5, cursor: 'pointer',
                  background: 'transparent', color: 'var(--text-muted)',
                  border: '1px solid var(--border)',
                }}
              >
                Cancel
              </button>
            </>
          )}
        </div>
      </div>

      {/* Body */}
      {open && (
        <div style={{ padding: mode === 'pending' ? '0.5rem 0.75rem' : '0' }}>
          {mode === 'pending'
            ? claims.map(c => <PendingClaimRow key={c.id} claim={c} onAction={onAction} />)
            : claims.map(c => <HistoryClaimRow key={c.id} claim={c} onDelete={onAction} />)
          }
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function AdminPage() {
  const auth = useAuth()
  const [pending, setPending] = useState<ClaimDetail[]>([])
  const [all, setAll] = useState<ClaimDetail[]>([])
  const [showAll, setShowAll] = useState(false)
  const [loading, setLoading] = useState(true)

  async function fetchPending() {
    setLoading(true)
    try {
      const res = await fetch('/api/admin/claims?status=pending', { credentials: 'include' })
      if (res.ok) setPending(await res.json())
    } finally {
      setLoading(false)
    }
  }

  async function fetchAll() {
    const res = await fetch('/api/admin/claims', { credentials: 'include' })
    if (res.ok) setAll(await res.json())
  }

  useEffect(() => {
    if (auth.status === 'authenticated' && auth.user.is_admin) {
      fetchPending()
    }
  }, [auth.status])

  useEffect(() => {
    if (showAll && auth.status === 'authenticated' && auth.user.is_admin) {
      fetchAll()
    }
  }, [showAll, auth.status])

  if (auth.status === 'loading') {
    return <main style={{ maxWidth: 720, margin: '3rem auto', padding: '0 1rem' }}><p>Loading…</p></main>
  }

  if (auth.status === 'unauthenticated' || !auth.user.is_admin) {
    return (
      <main style={{ maxWidth: 720, margin: '3rem auto', padding: '0 1rem' }}>
        <Link to="/" style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>← Back</Link>
        <p style={{ marginTop: '2rem', color: '#f87171' }}>Access denied.</p>
      </main>
    )
  }

  const pendingGroups = groupByUser(pending)
  const allGroups = groupByUser(all)

  return (
    <main style={{ maxWidth: 720, margin: '3rem auto', padding: '0 1rem' }}>
      <Link to="/" style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>← Back</Link>
      <h1 style={{ margin: '0.75rem 0 0.25rem' }}>Admin Panel</h1>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginBottom: '1.5rem' }}>
        Manage character claim requests.
      </p>

      {/* Pending queue */}
      <h2 style={{
        fontSize: '0.85rem', textTransform: 'uppercase', letterSpacing: '0.06em',
        color: 'var(--text-muted)', marginBottom: '0.6rem',
      }}>
        Pending claims {!loading && `(${pending.length})`}
      </h2>

      {loading && <p style={{ color: 'var(--text-muted)' }}>Loading…</p>}

      {!loading && pendingGroups.length === 0 && (
        <p style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>No pending claims.</p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {pendingGroups.map(g => (
          <UserSection
            key={g.discordId}
            discordId={g.discordId}
            claims={g.claims}
            mode="pending"
            defaultOpen={true}
            onAction={fetchPending}
          />
        ))}
      </div>

      {/* All claims history */}
      <div style={{ marginTop: '2rem' }}>
        <button
          onClick={() => setShowAll(v => !v)}
          style={{
            background: 'transparent', border: 'none', cursor: 'pointer',
            color: 'var(--text-muted)', fontSize: '0.85rem', padding: 0,
          }}
        >
          {showAll ? '▾ Hide history' : '▸ View all claims'}
        </button>

        {showAll && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.6rem' }}>
            {allGroups.length === 0
              ? <p style={{ color: 'var(--text-muted)', fontSize: '0.88rem' }}>No claims yet.</p>
              : allGroups.map(g => (
                  <UserSection
                    key={g.discordId}
                    discordId={g.discordId}
                    claims={g.claims}
                    mode="history"
                    defaultOpen={false}
                    onAction={fetchAll}
                  />
                ))
            }
          </div>
        )}
      </div>
    </main>
  )
}
