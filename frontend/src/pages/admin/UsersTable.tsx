import { useState, useMemo, useEffect } from 'react'
import { discordAvatarUrl } from '../../hooks/useAuth'
import { Badge, Button } from '../../components/ui'
import { FilterPill } from '../../components/FilterPill'
import { fmtRelative } from '../../formatters'
import {
  type UserItem,
  ACCESS_BADGE_VARIANT,
  TH_CLS, TD_CLS, TABLE_CLS,
  fmt,
} from './types'

// ── UserRow ───────────────────────────────────────────────────────────────────

function UserRow({ user, onAction }: { user: UserItem; onAction: () => void }) {
  const [busy, setBusy] = useState(false)
  const [kickConfirm, setKickConfirm] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function doAccess(action: 'approve' | 'deny' | 'kick') {
    setBusy(true)
    setError(null)
    try {
      const url = action === 'kick'
        ? `/api/admin/users/${user.discord_id}/kick`
        : `/api/admin/users/${user.discord_id}/${action}`
      const res = await fetch(url, { method: 'POST', credentials: 'include' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError(body.detail ?? `HTTP ${res.status}`)
        return
      }
      onAction()
    } finally {
      setBusy(false)
      setKickConfirm(false)
    }
  }

  // Grant / revoke role helper. The backend rejects unknown role names so the
  // UI just sends the literal — only 'contributor' wired today, but the shape
  // (POST to grant, DELETE to revoke) extends to future roles unchanged.
  async function toggleRole(role: string, grant: boolean) {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`/api/admin/users/${user.discord_id}/roles/${role}`, {
        method: grant ? 'POST' : 'DELETE',
        credentials: 'include',
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError(body.detail ?? `HTTP ${res.status}`)
        return
      }
      onAction()
    } finally {
      setBusy(false)
    }
  }

  const displayName   = user.discord_name ?? user.discord_username ?? 'Unknown'
  const badgeVariant = ACCESS_BADGE_VARIANT[user.access_status] ?? ACCESS_BADGE_VARIANT.denied

  return (
    <tr>
      {/* User */}
      <td className={TD_CLS}>
        <div className="flex items-center gap-2">
          <img
            src={discordAvatarUrl(user.discord_id, user.avatar)}
            alt=""
            width={28} height={28}
            className="rounded-full shrink-0"
          />
          <div className="min-w-0">
            <div className="font-semibold text-[0.88rem] leading-[1.2]">
              {displayName}
            </div>
            {user.discord_username && user.discord_username !== user.discord_name && (
              <div className="text-text-muted text-[0.72rem]">
                {user.discord_username}
              </div>
            )}
          </div>
        </div>
      </td>

      {/* Joined */}
      <td className={`${TD_CLS} text-text-muted whitespace-nowrap`}>
        <span title={fmt(user.first_seen)}>{fmtRelative(user.first_seen)}</span>
      </td>

      {/* Status */}
      <td className={TD_CLS}>
        <Badge variant={badgeVariant}>{user.access_status}</Badge>
      </td>

      {/* Claims */}
      <td className={`${TD_CLS} text-center ${user.claim_count ? 'text-text' : 'text-text-muted'}`}>
        {user.claim_count}
      </td>

      {/* Roles — chips + toggle. Contributor is the only DB-granted role
          today; the layout already accommodates a multi-chip future shape. */}
      <td className={TD_CLS}>
        <div className="flex items-center gap-1.5 flex-wrap">
          {user.roles.map(role => (
            <span
              key={role}
              className="text-[0.7rem] text-gold-bright bg-gold/15 border border-gold/40 rounded-sm px-1.5 py-[1px] uppercase tracking-[0.04em]"
            >
              {role}
            </span>
          ))}
          {user.roles.length === 0 && (
            <span className="text-text-muted text-[0.72rem]">—</span>
          )}
          {user.roles.includes('contributor') ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => toggleRole('contributor', false)}
              disabled={busy}
              title="Revoke contributor role"
            >
              Revoke contributor
            </Button>
          ) : (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => toggleRole('contributor', true)}
              disabled={busy}
              title="Grant the contributor role (can edit raid strategies + overviews)"
            >
              Make contributor
            </Button>
          )}
          {/* Supporter — cosmetic-only role surfaced as the 👑 badge.
              Awarded manually in recognition of GitHub Sponsors. No
              capability granted; revoking it just hides the badge. */}
          {user.roles.includes('supporter') ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => toggleRole('supporter', false)}
              disabled={busy}
              title="Revoke supporter role (removes the 👑 badge)"
            >
              Revoke supporter
            </Button>
          ) : (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => toggleRole('supporter', true)}
              disabled={busy}
              title="Grant the supporter role (adds the 👑 badge next to their name across the site)"
            >
              Make supporter
            </Button>
          )}
        </div>
      </td>

      {/* Actions */}
      <td className={`${TD_CLS} whitespace-nowrap`}>
        <div className="flex flex-col gap-1">
          {kickConfirm ? (
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[0.75rem] text-danger">Kick + delete all claims?</span>
              <Button variant="danger" size="sm" onClick={() => doAccess('kick')} disabled={busy}>
                {busy ? '…' : 'Confirm'}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setKickConfirm(false)}>Cancel</Button>
            </div>
          ) : (
            <div className="flex gap-1.5 flex-wrap">
              {user.access_status !== 'approved' && (
                <Button variant="primary" size="sm" onClick={() => doAccess('approve')} disabled={busy}>
                  Approve
                </Button>
              )}
              {user.access_status !== 'denied' && (
                <Button variant="danger" size="sm" onClick={() => doAccess('deny')} disabled={busy}>
                  Deny
                </Button>
              )}
              <Button
                variant="danger"
                size="sm"
                onClick={() => setKickConfirm(true)}
                disabled={busy}
                title="Revoke access and delete all claims"
              >
                Kick
              </Button>
            </div>
          )}
          {error && <div className="text-danger text-[0.78rem] mt-1">{error}</div>}
        </div>
      </td>
    </tr>
  )
}

// ── UsersTable ────────────────────────────────────────────────────────────────

const PER_PAGE = 10

export function UsersTable({ users, onAction }: { users: UserItem[]; onAction: () => void }) {
  const [filter, setFilter] = useState<'all' | 'pending' | 'approved' | 'denied'>('all')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)

  const counts = {
    all:      users.length,
    pending:  users.filter(u => u.access_status === 'pending').length,
    approved: users.filter(u => u.access_status === 'approved').length,
    denied:   users.filter(u => u.access_status === 'denied').length,
  }

  // Status pill + free-text search (display name / username / discord id).
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return users.filter(u => {
      if (filter !== 'all' && u.access_status !== filter) return false
      if (!q) return true
      return (
        (u.discord_name ?? '').toLowerCase().includes(q) ||
        (u.discord_username ?? '').toLowerCase().includes(q) ||
        u.discord_id.includes(q)
      )
    })
  }, [users, filter, search])

  // Changing the filter or search jumps back to the first page.
  useEffect(() => { setPage(1) }, [filter, search])

  const pageCount = Math.max(1, Math.ceil(filtered.length / PER_PAGE))
  const safePage  = Math.min(page, pageCount)  // clamp if the list shrank (e.g. after a kick)
  const start     = (safePage - 1) * PER_PAGE
  const pageRows  = filtered.slice(start, start + PER_PAGE)

  return (
    <div>
      {/* Filter pills + search */}
      <div className="flex gap-2 mb-3 flex-wrap items-center">
        <div className="flex gap-1.5 flex-wrap">
          {(['all', 'pending', 'approved', 'denied'] as const).map(f => (
            <FilterPill key={f} active={filter === f} onClick={() => setFilter(f)}>
              {f.charAt(0).toUpperCase() + f.slice(1)} <span className="opacity-70 text-[0.7rem]">({counts[f]})</span>
            </FilterPill>
          ))}
        </div>
        <input
          type="search"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search name or username…"
          aria-label="Search users"
          className="ml-auto w-full sm:w-[220px] text-[0.85rem] py-1 px-2.5 rounded-sm2 border border-border bg-surface-raised text-text"
        />
      </div>

      <div className="overflow-x-auto border border-border rounded-md">
        <table className={TABLE_CLS}>
          <thead>
            <tr className="bg-white/2">
              <th className={TH_CLS}>User</th>
              <th className={TH_CLS}>Joined</th>
              <th className={TH_CLS}>Status</th>
              <th className={`${TH_CLS} text-center`}>Claims</th>
              <th className={TH_CLS}>Roles</th>
              <th className={TH_CLS}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {pageRows.length === 0 ? (
              <tr>
                <td colSpan={6} className={`${TD_CLS} text-text-muted text-center p-6`}>
                  {users.length === 0 ? 'No users.' : 'No users match.'}
                </td>
              </tr>
            ) : (
              pageRows.map(u => (
                <UserRow key={u.discord_id} user={u} onAction={onAction} />
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {filtered.length > PER_PAGE && (
        <div className="flex items-center justify-between mt-3 text-[0.8rem] text-text-muted">
          <span>
            Showing {start + 1}–{Math.min(start + PER_PAGE, filtered.length)} of {filtered.length}
          </span>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>
              Prev
            </Button>
            <span>Page {safePage} of {pageCount}</span>
            <Button variant="ghost" size="sm" disabled={safePage >= pageCount} onClick={() => setPage(safePage + 1)}>
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
