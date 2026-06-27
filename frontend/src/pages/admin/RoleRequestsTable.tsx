import { useState } from 'react'
import { discordAvatarUrl } from '../../hooks/useAuth'
import { usePagedSearch } from '../../hooks/usePagedSearch'
import { Button } from '../../components/ui'
import { TablePager, TableSearch } from './TableControls'
import { fmtRelative } from '../../formatters'
import {
  type RoleRequest,
  TH_CLS, TD_CLS, TABLE_CLS,
  fmt,
} from './types'

// ── RoleRequestRow ────────────────────────────────────────────────────────────

function RoleRequestRow({ request, onAction }: { request: RoleRequest; onAction: () => void }) {
  const [busy, setBusy] = useState(false)
  const [noteOpen, setNoteOpen] = useState<'approve' | 'reject' | null>(null)
  const [adminNote, setAdminNote] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function decide(action: 'approve' | 'reject') {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`/api/admin/role-requests/${request.id}/${action}`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: adminNote.trim() || null }),
      })
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}))
        setError(errBody.detail ?? `HTTP ${res.status}`)
        return
      }
      onAction()
    } finally {
      setBusy(false)
      setNoteOpen(null)
      setAdminNote('')
    }
  }

  const displayName = request.discord_name ?? request.discord_username ?? 'Unknown'

  return (
    <tr>
      <td className={TD_CLS}>
        <div className="flex items-center gap-2">
          <img
            src={discordAvatarUrl(request.discord_id, request.avatar)}
            alt=""
            width={28} height={28}
            className="rounded-full shrink-0"
          />
          <div className="min-w-0">
            <div className="font-semibold text-[0.88rem] leading-[1.2]">{displayName}</div>
            {request.discord_username && request.discord_username !== request.discord_name && (
              <div className="text-text-muted text-[0.72rem]">{request.discord_username}</div>
            )}
          </div>
        </div>
      </td>
      <td className={`${TD_CLS} capitalize`}>{request.role}</td>
      <td className={`${TD_CLS} text-text-muted whitespace-nowrap`}>
        <span title={fmt(request.requested_at)}>{fmtRelative(request.requested_at)}</span>
      </td>
      <td className={`${TD_CLS} text-text-muted text-[0.82rem] max-w-[28rem]`}>
        {request.user_note ? <em>"{request.user_note}"</em> : '—'}
      </td>
      <td className={`${TD_CLS} whitespace-nowrap`}>
        <div className="flex flex-col gap-1">
          {noteOpen ? (
            <div className="flex flex-col gap-1 w-full min-w-[12rem]">
              <textarea
                value={adminNote}
                onChange={e => setAdminNote(e.target.value)}
                rows={2}
                placeholder={noteOpen === 'approve' ? 'Optional note (e.g. welcome message)' : 'Optional reason (visible to the requester)'}
                className="w-full bg-bg/60 border border-border rounded-md p-2 text-[0.82rem] text-text outline-none focus:border-gold/60 resize-y"
              />
              <div className="flex items-center gap-1.5 justify-end">
                <Button variant="ghost" size="sm" onClick={() => { setNoteOpen(null); setAdminNote('') }} disabled={busy}>Cancel</Button>
                <Button
                  variant={noteOpen === 'approve' ? 'primary' : 'danger'}
                  size="sm"
                  onClick={() => decide(noteOpen)}
                  disabled={busy}
                >
                  {busy ? '…' : noteOpen === 'approve' ? 'Confirm approve' : 'Confirm reject'}
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex gap-1.5 flex-wrap">
              <Button variant="primary" size="sm" disabled={busy} onClick={() => setNoteOpen('approve')}>
                Approve
              </Button>
              <Button variant="danger" size="sm" disabled={busy} onClick={() => setNoteOpen('reject')}>
                Reject
              </Button>
            </div>
          )}
          {error && <div className="text-danger text-[0.78rem] mt-1">{error}</div>}
        </div>
      </td>
    </tr>
  )
}

// ── RoleRequestsTable ─────────────────────────────────────────────────────────

const requestMatches = (r: RoleRequest, q: string) =>
  (r.discord_name ?? '').toLowerCase().includes(q) ||
  (r.discord_username ?? '').toLowerCase().includes(q) ||
  r.role.toLowerCase().includes(q) ||
  r.discord_id.includes(q)

export function RoleRequestsTable({ requests, onAction }: { requests: RoleRequest[]; onAction: () => void }) {
  const pg = usePagedSearch(requests, requestMatches)

  return (
    <div>
      {/* Search — only worth showing once it would actually paginate */}
      {requests.length > pg.perPage && (
        <div className="flex mb-3">
          <TableSearch value={pg.search} onChange={pg.setSearch} placeholder="Search requester or role…" />
        </div>
      )}

      <div className="bg-surface border border-border rounded-[10px] overflow-x-auto">
        <table className={TABLE_CLS}>
          <thead>
            <tr>
              <th className={TH_CLS}>Requester</th>
              <th className={TH_CLS}>Role</th>
              <th className={TH_CLS}>Submitted</th>
              <th className={TH_CLS}>Note</th>
              <th className={TH_CLS}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {pg.rows.length === 0 ? (
              <tr>
                <td colSpan={5} className={`${TD_CLS} text-text-muted text-center p-6`}>
                  {requests.length === 0 ? 'No pending role requests.' : 'No requests match.'}
                </td>
              </tr>
            ) : (
              pg.rows.map(r => <RoleRequestRow key={r.id} request={r} onAction={onAction} />)
            )}
          </tbody>
        </table>
      </div>

      <TablePager
        page={pg.page} pageCount={pg.pageCount} start={pg.start}
        perPage={pg.perPage} total={pg.total} onPage={pg.setPage}
      />
    </div>
  )
}
