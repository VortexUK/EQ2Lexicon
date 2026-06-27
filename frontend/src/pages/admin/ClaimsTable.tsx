import { useState } from 'react'
import { discordAvatarUrl } from '../../hooks/useAuth'
import { usePagedSearch } from '../../hooks/usePagedSearch'
import { Badge, Button } from '../../components/ui'
import { FilterPill } from '../../components/FilterPill'
import { TablePager, TableSearch } from './TableControls'
import { fmtRelative } from '../../formatters'
import {
  type ClaimDetail,
  CLAIM_BADGE_VARIANT,
  TH_CLS, TD_CLS, TABLE_CLS,
  fmt,
} from './types'

// ── ClaimRow ──────────────────────────────────────────────────────────────────

function ClaimRow({ claim, onDelete }: { claim: ClaimDetail; onDelete: () => void }) {
  const [rejectOpen, setRejectOpen] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function doAction(url: string, body?: object | null, method = 'POST') {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(url, {
        method,
        credentials: 'include',
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      })
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}))
        setError(errBody.detail ?? `HTTP ${res.status}`)
        return
      }
      onDelete()
    } finally {
      setBusy(false)
      setRejectOpen(false)
    }
  }

  const displayName   = claim.discord_name ?? claim.discord_username ?? claim.discord_id
  const badgeVariant = CLAIM_BADGE_VARIANT[claim.status] ?? CLAIM_BADGE_VARIANT.withdrawn

  return (
    <tr>
      {/* Character */}
      <td className={`${TD_CLS} text-gold font-semibold`}>
        {claim.character_name}
      </td>

      {/* User */}
      <td className={TD_CLS}>
        <div className="flex items-center gap-1.5">
          <img
            src={discordAvatarUrl(claim.discord_id, claim.avatar)}
            alt=""
            width={22} height={22}
            className="rounded-full shrink-0"
          />
          <span className="text-[0.85rem]">{displayName}</span>
        </div>
      </td>

      {/* Status */}
      <td className={TD_CLS}>
        <Badge variant={badgeVariant}>{claim.status}</Badge>
      </td>

      {/* Submitted */}
      <td className={`${TD_CLS} text-text-muted whitespace-nowrap`}>
        <span title={fmt(claim.requested_at)}>{fmtRelative(claim.requested_at)}</span>
      </td>

      {/* Reviewed */}
      <td className={`${TD_CLS} text-text-muted whitespace-nowrap`}>
        {claim.reviewed_at ? (
          <span title={fmt(claim.reviewed_at)}>{fmtRelative(claim.reviewed_at)}</span>
        ) : (
          <span className="opacity-40">—</span>
        )}
      </td>

      {/* Note */}
      <td className={`${TD_CLS} text-text-muted italic text-[0.8rem] max-w-[180px]`}>
        {claim.note
          ? <span title={claim.note} className="block overflow-hidden text-ellipsis whitespace-nowrap">"{claim.note}"</span>
          : <span className="opacity-40">—</span>
        }
      </td>

      {/* Actions */}
      <td className={`${TD_CLS} whitespace-nowrap`}>
        <div className="flex flex-col gap-1">
        {claim.status === 'pending' ? (
          rejectOpen ? (
            <div className="flex flex-col gap-1 min-w-[200px]">
              <textarea
                placeholder="Optional rejection reason…"
                value={note}
                onChange={e => setNote(e.target.value)}
                rows={2}
                className="text-[0.78rem] resize-y w-full box-border"
              />
              <div className="flex gap-1">
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => doAction(`/api/admin/claims/${claim.id}/reject`, { note: note || null })}
                  disabled={busy}
                >
                  {busy ? '…' : 'Confirm'}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => { setRejectOpen(false); setNote('') }}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex gap-1.5">
              <Button
                variant="primary"
                size="sm"
                onClick={() => doAction(`/api/admin/claims/${claim.id}/approve`)}
                disabled={busy}
              >
                Approve
              </Button>
              <Button variant="danger" size="sm" onClick={() => setRejectOpen(true)} disabled={busy}>
                Reject
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => doAction(`/api/admin/claims/${claim.id}`, null, 'DELETE')}
                disabled={busy}
                title="Delete permanently"
              >
                🗑
              </Button>
            </div>
          )
        ) : (
          <Button
            variant="ghost"
            size="icon"
            onClick={() => doAction(`/api/admin/claims/${claim.id}`, null, 'DELETE')}
            disabled={busy}
            title="Delete permanently"
          >
            🗑
          </Button>
        )}
        {error && <div className="text-danger text-[0.78rem] mt-1">{error}</div>}
        </div>
      </td>
    </tr>
  )
}

// ── ClaimsTable ───────────────────────────────────────────────────────────────

const claimMatches = (c: ClaimDetail, q: string) =>
  c.character_name.toLowerCase().includes(q) ||
  (c.discord_name ?? '').toLowerCase().includes(q) ||
  (c.discord_username ?? '').toLowerCase().includes(q) ||
  c.discord_id.includes(q)

export function ClaimsTable({ claims, onAction }: { claims: ClaimDetail[]; onAction: () => void }) {
  const [filter, setFilter] = useState<'pending' | 'all'>('pending')

  const pendingCount = claims.filter(c => c.status === 'pending').length
  const statusFiltered = filter === 'pending' ? claims.filter(c => c.status === 'pending') : claims
  const pg = usePagedSearch(statusFiltered, claimMatches, { filterKey: filter })

  return (
    <div>
      {/* Filter pills + search */}
      <div className="flex gap-2 mb-3 flex-wrap items-center">
        <div className="flex gap-1.5 flex-wrap">
          {(['pending', 'all'] as const).map(f => {
            const count = f === 'pending' ? pendingCount : claims.length
            return (
              <FilterPill key={f} active={filter === f} onClick={() => setFilter(f)}>
                {f === 'pending' ? 'Pending' : 'All'} <span className="opacity-70 text-[0.7rem]">({count})</span>
              </FilterPill>
            )
          })}
        </div>
        <TableSearch value={pg.search} onChange={pg.setSearch} placeholder="Search character or user…" />
      </div>

      <div className="overflow-x-auto border border-border rounded-md">
        <table className={TABLE_CLS}>
          <thead>
            <tr className="bg-white/2">
              <th className={TH_CLS}>Character</th>
              <th className={TH_CLS}>Discord user</th>
              <th className={TH_CLS}>Status</th>
              <th className={TH_CLS}>Submitted</th>
              <th className={TH_CLS}>Reviewed</th>
              <th className={TH_CLS}>Note</th>
              <th className={TH_CLS}></th>
            </tr>
          </thead>
          <tbody>
            {pg.rows.length === 0 ? (
              <tr>
                <td colSpan={7} className={`${TD_CLS} text-text-muted text-center p-6`}>
                  {claims.length === 0
                    ? 'No claims yet.'
                    : filter === 'pending'
                      ? 'No pending claims.'
                      : 'No claims match.'}
                </td>
              </tr>
            ) : (
              pg.rows.map(c => (
                <ClaimRow key={c.id} claim={c} onDelete={onAction} />
              ))
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
