import { useEffect, useMemo, useState } from 'react'

import Breadcrumb from '../components/Breadcrumb'
import { Button, Card, SectionLabel } from '../components/ui'
import { fmtLocalDateTime, fmtRelative } from '../formatters'
import { useAuth } from '../hooks/useAuth'

// ── Types ─────────────────────────────────────────────────────────────────────

interface RoleRequest {
  id: number
  discord_id: string
  discord_name: string | null
  discord_username: string | null
  avatar: string | null
  role: string
  status: 'pending' | 'approved' | 'rejected' | 'withdrawn'
  requested_at: number
  reviewed_at: number | null
  reviewed_by: string | null
  user_note: string | null
  admin_note: string | null
}

// Available roles + their descriptions. Hardcoded for now since there's only
// one — when there are more, a /api/roles catalogue endpoint slots in here.
const AVAILABLE_ROLES: { key: string; label: string; description: string }[] = [
  {
    key: 'contributor',
    label: 'Contributor',
    description:
      "Can edit raid strategies and zone overviews. Requests are reviewed by an admin — leave a note if you'd like to add context.",
  },
]

const STATUS_LABELS: Record<RoleRequest['status'], string> = {
  pending:   'Pending',
  approved:  'Approved',
  rejected:  'Rejected',
  withdrawn: 'Withdrawn',
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function RolesSettingsPage() {
  const auth = useAuth()
  const [requests, setRequests] = useState<RoleRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function refresh() {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch('/api/me/role-requests', { credentials: 'include' })
      if (r.status === 401) {
        // The Layout already guards unauthenticated viewers — surfacing
        // anything here would just be noise.
        setRequests([])
        return
      }
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
      setRequests((await r.json()) as RoleRequest[])
    } catch (err) {
      setError(String((err as Error).message ?? err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  // Latest request per role, used by the role cards to decide which CTA to
  // render. Sort is newest-first courtesy of the backend, so the first match
  // per role is the freshest.
  const latestByRole = useMemo(() => {
    const out: Record<string, RoleRequest> = {}
    for (const req of requests) {
      if (!(req.role in out)) out[req.role] = req
    }
    return out
  }, [requests])

  const myStaticRoles =
    auth.status === 'authenticated' ? auth.user.static_roles : []
  const isAdmin = auth.status === 'authenticated' && auth.user.is_admin

  return (
    <main className="page-enter mx-auto max-w-3xl px-4 py-6">
      <Breadcrumb items={[{ label: 'Settings' }, { label: 'Roles' }]} />
      <h1 className="font-heading text-[1.7rem] text-gold mb-1">Roles</h1>
      <p className="text-text-muted text-sm mb-5">
        Roles control what you can edit on the site. Most can be self-requested
        for admin review.
        {isAdmin && (
          <>
            {' '}You are an <span className="text-gold">admin</span>; granting and
            review live on the <a href="/admin" className="text-gold underline decoration-dotted underline-offset-2">admin panel</a>.
          </>
        )}
      </p>

      <section className="flex flex-col gap-4">
        <SectionLabel>Available roles</SectionLabel>
        {AVAILABLE_ROLES.map(role => (
          <RoleCard
            key={role.key}
            role={role}
            granted={myStaticRoles.includes(role.key)}
            latestRequest={latestByRole[role.key] ?? null}
            onChanged={refresh}
            adminGrantedDirect={isAdmin && myStaticRoles.includes(role.key)}
          />
        ))}
      </section>

      <section className="mt-7">
        <SectionLabel>Request history</SectionLabel>
        {loading && <p className="text-text-muted text-sm">Loading…</p>}
        {error && <p className="text-danger text-sm">Failed to load: {error}</p>}
        {!loading && !error && requests.length === 0 && (
          <p className="text-text-muted text-sm">You haven't submitted any role requests yet.</p>
        )}
        {!loading && requests.length > 0 && (
          <RequestHistoryTable requests={requests} />
        )}
      </section>
    </main>
  )
}

// ── Role card ─────────────────────────────────────────────────────────────────

interface RoleCardProps {
  role: { key: string; label: string; description: string }
  /** True if the user currently holds this role (from /auth/me static_roles). */
  granted: boolean
  /** Most recent request the user submitted for this role, or null. */
  latestRequest: RoleRequest | null
  /** True for admins who already hold the role via direct grant — shows a
   *  reassuring "you already have this" rather than a Request button. */
  adminGrantedDirect: boolean
  onChanged: () => void
}

function RoleCard({ role, granted, latestRequest, adminGrantedDirect, onChanged }: RoleCardProps) {
  const [draftOpen, setDraftOpen] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      const r = await fetch('/api/me/role-requests', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: role.key, note: note.trim() || null }),
      })
      if (!r.ok) {
        const j = await r.json().catch(() => ({}))
        throw new Error(j.detail ?? `${r.status} ${r.statusText}`)
      }
      setDraftOpen(false)
      setNote('')
      onChanged()
    } catch (err) {
      setError(String((err as Error).message ?? err))
    } finally {
      setBusy(false)
    }
  }

  async function withdraw(requestId: number) {
    setBusy(true)
    setError(null)
    try {
      const r = await fetch(`/api/me/role-requests/${requestId}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
      onChanged()
    } catch (err) {
      setError(String((err as Error).message ?? err))
    } finally {
      setBusy(false)
    }
  }

  // Decide the right CTA. Granted always wins; otherwise the freshest request
  // status drives the UI. Cold (no history) = a request button + optional note.
  const pending = !granted && latestRequest?.status === 'pending'

  return (
    <Card className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h3 className="font-heading text-gold-bright text-[1.05rem]">{role.label}</h3>
          <p className="text-text-muted text-sm leading-relaxed mt-1">{role.description}</p>
        </div>
        <StatusBadge granted={granted} latestRequest={latestRequest} />
      </div>

      {granted && (
        <p className="text-text-muted text-[0.78rem]">
          {adminGrantedDirect
            ? 'You hold this role via the admin allow-list.'
            : 'You hold this role.'}
        </p>
      )}

      {pending && latestRequest && (
        <div className="text-text-muted text-[0.78rem] flex items-center gap-3 flex-wrap">
          <span>
            Submitted {fmtRelative(latestRequest.requested_at)}
            {latestRequest.user_note && <>: <em>"{latestRequest.user_note}"</em></>}
          </span>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => withdraw(latestRequest.id)}>
            Withdraw
          </Button>
        </div>
      )}

      {!granted && !pending && (
        <>
          {latestRequest?.status === 'rejected' && (
            <p className="text-text-muted text-[0.78rem]">
              Previous request rejected {fmtRelative(latestRequest.reviewed_at ?? latestRequest.requested_at)}
              {latestRequest.admin_note && <>: <em>"{latestRequest.admin_note}"</em></>}.
            </p>
          )}
          {!draftOpen ? (
            <div>
              <Button size="sm" variant="secondary" onClick={() => setDraftOpen(true)}>
                {latestRequest?.status === 'rejected' ? 'Request again' : 'Request'}
              </Button>
            </div>
          ) : (
            <div className="flex flex-col gap-2 mt-1">
              <textarea
                value={note}
                onChange={e => setNote(e.target.value)}
                rows={3}
                maxLength={2000}
                placeholder="Optional: a short note for the admin reviewing your request."
                className="w-full bg-bg/60 border border-border rounded-md p-2 text-[0.88rem] text-text outline-none focus:border-gold/60 resize-y"
              />
              <div className="flex items-center gap-2 justify-end">
                <Button size="sm" variant="ghost" onClick={() => { setDraftOpen(false); setNote(''); setError(null) }} disabled={busy}>
                  Cancel
                </Button>
                <Button size="sm" variant="primary" onClick={submit} disabled={busy}>
                  {busy ? 'Submitting…' : 'Submit request'}
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      {error && <p className="text-danger text-[0.78rem]">{error}</p>}
    </Card>
  )
}

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ granted, latestRequest }: { granted: boolean; latestRequest: RoleRequest | null }) {
  // Granted always wins — direct admin grants don't have a request row.
  if (granted) {
    return <Badge label="Active" tone="success" />
  }
  if (!latestRequest) {
    return <Badge label="Not requested" tone="muted" />
  }
  const tone =
    latestRequest.status === 'pending'   ? 'gold'
  : latestRequest.status === 'rejected'  ? 'danger'
  : 'muted'
  return <Badge label={STATUS_LABELS[latestRequest.status]} tone={tone} />
}

function Badge({ label, tone }: { label: string; tone: 'success' | 'gold' | 'danger' | 'muted' }) {
  const cls =
    tone === 'success' ? 'bg-success/15 border-success/40 text-success'
  : tone === 'gold'    ? 'bg-gold/15 border-gold/40 text-gold-bright'
  : tone === 'danger'  ? 'bg-danger/15 border-danger/40 text-danger'
  : 'bg-surface-raised/60 border-border text-text-muted'
  return (
    <span className={`text-[0.7rem] uppercase tracking-[0.05em] border rounded-sm px-1.5 py-[1px] shrink-0 ${cls}`}>
      {label}
    </span>
  )
}

// ── History table ─────────────────────────────────────────────────────────────

function RequestHistoryTable({ requests }: { requests: RoleRequest[] }) {
  return (
    <Card className="p-0 overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-text-muted text-left text-[0.7rem] uppercase tracking-wide border-b border-border">
            <th className="px-3 py-2">Role</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Submitted</th>
            <th className="px-3 py-2">Reviewed</th>
            <th className="px-3 py-2">Notes</th>
          </tr>
        </thead>
        <tbody>
          {requests.map(req => (
            <tr key={req.id} className="border-b border-border/40 align-top">
              <td className="px-3 py-2 capitalize">{req.role}</td>
              <td className="px-3 py-2">
                <Badge
                  label={STATUS_LABELS[req.status]}
                  tone={
                    req.status === 'approved'  ? 'success'
                  : req.status === 'pending'   ? 'gold'
                  : req.status === 'rejected'  ? 'danger'
                  : 'muted'
                  }
                />
              </td>
              <td className="px-3 py-2 text-text-muted whitespace-nowrap" title={fmtLocalDateTime(req.requested_at)}>
                {fmtRelative(req.requested_at)}
              </td>
              <td className="px-3 py-2 text-text-muted whitespace-nowrap">
                {req.reviewed_at ? (
                  <span title={fmtLocalDateTime(req.reviewed_at)}>{fmtRelative(req.reviewed_at)}</span>
                ) : '—'}
              </td>
              <td className="px-3 py-2 text-text-muted text-[0.82rem]">
                {req.user_note && <div>You: <em>"{req.user_note}"</em></div>}
                {req.admin_note && <div>Admin: <em>"{req.admin_note}"</em></div>}
                {!req.user_note && !req.admin_note && <span>—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}
