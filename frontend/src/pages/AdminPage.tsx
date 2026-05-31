import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { UsersTable } from './admin/UsersTable'
import { ClaimsTable } from './admin/ClaimsTable'
import { RoleRequestsTable } from './admin/RoleRequestsTable'
import { ParsesAdminTable } from './admin/ParsesAdminTable'
import { ServersSection } from './admin/ServersSection'
import { TamperReportsTable } from './admin/TamperReportsTable'
import type { UserItem, ClaimDetail, RoleRequest } from './admin/types'
import { SECTION_TITLE_CLS } from './admin/types'

// ── AdminPage ─────────────────────────────────────────────────────────────────

const SECTION_CLS = 'mb-10'

export default function AdminPage() {
  const auth = useAuth()
  const [users,        setUsers]        = useState<UserItem[]>([])
  const [claims,       setClaims]       = useState<ClaimDetail[]>([])
  const [roleRequests, setRoleRequests] = useState<RoleRequest[]>([])
  const [loading,      setLoading]      = useState(true)
  const [error,        setError]        = useState<string | null>(null)

  async function fetchAll() {
    setLoading(true)
    setError(null)
    try {
      const [uRes, cRes, rrRes] = await Promise.all([
        fetch('/api/admin/users',         { credentials: 'include' }),
        fetch('/api/admin/claims',        { credentials: 'include' }),
        fetch('/api/admin/role-requests', { credentials: 'include' }),
      ])
      if (!uRes.ok || !cRes.ok || !rrRes.ok) {
        const firstFailed = !uRes.ok ? uRes : !cRes.ok ? cRes : rrRes
        const body = await firstFailed.json().catch(() => ({}))
        setError(`Error: ${body.detail ?? 'Failed to load admin data'}`)
        return
      }
      const [u, c, rr] = await Promise.all([uRes.json(), cRes.json(), rrRes.json()])
      setUsers(u)
      setClaims(c)
      setRoleRequests(rr)
    } catch {
      setError('Network error — could not load admin data.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (auth.status === 'authenticated' && auth.user.is_admin) {
      fetchAll()
    }
  }, [auth.status])

  if (auth.status === 'loading') {
    return (
      <main className="max-w-[960px] mx-auto my-12 px-4">
        <p className="text-text-muted">Loading…</p>
      </main>
    )
  }

  if (auth.status === 'unauthenticated' || !auth.user.is_admin) {
    return (
      <main className="max-w-[960px] mx-auto my-12 px-4">
        <p className="mt-8 text-danger">Access denied.</p>
      </main>
    )
  }

  return (
    <main className="max-w-[1100px] mx-auto my-8 px-4">
      <Link to="/" className="text-text-muted text-[0.9rem]">← Back</Link>
      <h1 className="mt-2.5 mb-1 font-heading">Admin Panel</h1>
      <p className="text-text-muted text-[0.9rem] mb-7">
        Manage users and character claims.
      </p>

      {loading && <p className="text-text-muted">Loading…</p>}
      {error   && <p className="text-danger">{error}</p>}

      {!loading && !error && (
        <>
          {/* Pending role requests — surfaced first since admin attention is
              the bottleneck of the flow. Hidden entirely when empty. */}
          {roleRequests.length > 0 && (
            <div className={SECTION_CLS}>
              <p className={SECTION_TITLE_CLS}>
                Pending role requests ({roleRequests.length})
              </p>
              <RoleRequestsTable requests={roleRequests} onAction={fetchAll} />
            </div>
          )}

          {/* Users */}
          <div className={SECTION_CLS}>
            <p className={SECTION_TITLE_CLS}>
              Users ({users.length})
            </p>
            <UsersTable users={users} onAction={fetchAll} />
          </div>

          {/* Claims */}
          <div className={SECTION_CLS}>
            <p className={SECTION_TITLE_CLS}>
              Character claims ({claims.length})
            </p>
            <ClaimsTable claims={claims} onAction={fetchAll} />
          </div>

          {/* Servers */}
          <div className={SECTION_CLS}>
            <ServersSection />
          </div>

          {/* Tamper reports — surfaced above the parses-sanitize table
              because pending reports are the highest-signal admin work:
              they represent active attempts to upload tampered parses
              that the plugin already blocked at the source. The table
              manages its own fetch + acknowledge flow. */}
          <div className={SECTION_CLS}>
            <TamperReportsTable />
          </div>

          {/* Parses (sanitize) */}
          <div className={SECTION_CLS}>
            <ParsesAdminTable />
          </div>
        </>
      )}
    </main>
  )
}
