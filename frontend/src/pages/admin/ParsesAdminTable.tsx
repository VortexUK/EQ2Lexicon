import { useCallback, useEffect, useState } from 'react'
import { Button } from '../../components/ui'
import { fmtLocalDate } from '../../formatters'
import {
  type AdminParse,
  SECTION_TITLE_CLS,
  TH_CLS, TD_CLS, TABLE_CLS,
} from './types'

// ── helpers ───────────────────────────────────────────────────────────────────

function resultLabel(successLevel: number): string {
  switch (successLevel) {
    case 1:  return 'win'
    case 2:  return 'loss'
    case 3:  return 'mixed'
    default: return '—'
  }
}

// URL-length safety cap for the batch-purge endpoint.
const PARSE_BATCH_CHUNK_SIZE = 64

// Trash = an encounter whose title starts lowercase ("a krait warrior");
// named bosses start uppercase. Mirrors isBoss() in ParsesPage and the
// authoritative is_boss() server-side. Inlined here so the admin chunk
// doesn't pull in the whole ParsesPage module.
const isTrashTitle = (title: string): boolean => !/^[A-Z]/.test(title)

// ── ParsesAdminTable ──────────────────────────────────────────────────────────

export function ParsesAdminTable() {
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')          // committed search term that drives fetch
  const [rows, setRows] = useState<AdminParse[]>([])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setError(null)
    try {
      const url = `/api/admin/parses?search=${encodeURIComponent(query)}`
      const res = await fetch(url, { credentials: 'include', signal })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError(`Error: ${body.detail ?? 'Failed to load parses'}`)
        return
      }
      const data: AdminParse[] = await res.json()
      setRows(data)
      setSelected(new Set())
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return
      setError('Network error — could not load parses.')
    } finally {
      setLoading(false)
    }
  }, [query])

  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
    return () => controller.abort()
  }, [load])

  function toggleRow(id: number) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const allSelected = rows.length > 0 && rows.every(r => selected.has(r.id))
  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(rows.map(r => r.id)))
  }

  const trashCount = rows.reduce((n, r) => (isTrashTitle(r.title) ? n + 1 : n), 0)
  function selectAllTrash() {
    setSelected(new Set(rows.filter(r => isTrashTitle(r.title)).map(r => r.id)))
  }

  async function purgeOne(p: AdminParse) {
    if (!confirm(`Permanently delete "${p.title}"? This removes it from all leaderboards and cannot be undone.`)) return
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`/api/parses/${p.id}?purge=1`, { method: 'DELETE', credentials: 'include' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError(`Error: ${body.detail ?? 'Purge failed'}`)
        return
      }
      await load()
    } catch {
      setError('Network error — purge failed.')
    } finally {
      setBusy(false)
    }
  }

  async function purgeSelected() {
    const ids = [...selected]
    if (ids.length === 0) return
    if (!confirm(`Permanently delete ${ids.length} parse${ids.length === 1 ? '' : 's'}? This cannot be undone.`)) return
    setBusy(true)
    setError(null)
    try {
      for (let i = 0; i < ids.length; i += PARSE_BATCH_CHUNK_SIZE) {
        const chunk = ids.slice(i, i + PARSE_BATCH_CHUNK_SIZE)
        const url = `/api/parses/batch?ids=${chunk.join(',')}&purge=1`
        const res = await fetch(url, { method: 'DELETE', credentials: 'include' })
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          setError(`Error: ${body.detail ?? 'Bulk purge failed'}`)
          return
        }
      }
      setSelected(new Set())
      await load()
    } catch {
      setError('Network error — bulk purge failed.')
    } finally {
      setBusy(false)
    }
  }

  const selectedCount = selected.size

  return (
    <div>
      <p className={SECTION_TITLE_CLS}>
        Parses ({rows.length})
      </p>

      {/* Search + bulk action bar */}
      <form
        onSubmit={e => { e.preventDefault(); setQuery(search) }}
        className="flex items-center gap-2 mb-3 flex-wrap"
      >
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search title, zone, guild, uploader…"
          className="flex-1 min-w-[220px] bg-surface border border-border rounded-sm px-3 py-1.5 text-[0.875rem] text-text"
        />
        <Button variant="secondary" size="sm" type="submit" disabled={busy}>
          Search
        </Button>
        <Button
          variant="secondary"
          size="sm"
          type="button"
          onClick={selectAllTrash}
          disabled={busy || trashCount === 0}
          title="Select every trash encounter (lowercase title) on this page"
        >
          Select all trash ({trashCount})
        </Button>
        <Button
          variant="danger"
          size="sm"
          type="button"
          onClick={purgeSelected}
          disabled={busy || selectedCount === 0}
        >
          Purge selected ({selectedCount})
        </Button>
      </form>

      {error && <p className="text-danger mb-2">{error}</p>}

      <div className="overflow-x-auto border border-border rounded-md">
        <table className={TABLE_CLS}>
          <thead>
            <tr className="bg-white/2">
              <th className={`${TH_CLS} w-[1%]`}>
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleAll}
                  aria-label="Select all visible parses"
                  disabled={rows.length === 0}
                />
              </th>
              <th className={TH_CLS}>Title</th>
              <th className={TH_CLS}>Zone</th>
              <th className={TH_CLS}>Guild</th>
              <th className={TH_CLS}>Uploader</th>
              <th className={TH_CLS}>Date</th>
              <th className={`${TH_CLS} text-center`}>Players</th>
              <th className={TH_CLS}>Result</th>
              <th className={TH_CLS}></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={9} className={`${TD_CLS} text-text-muted text-center p-6`}>Loading…</td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={9} className={`${TD_CLS} text-text-muted text-center p-6`}>
                  {query ? 'No parses match the search.' : 'No parses.'}
                </td>
              </tr>
            ) : (
              rows.map(p => (
                <tr key={p.id}>
                  <td className={TD_CLS}>
                    <input
                      type="checkbox"
                      checked={selected.has(p.id)}
                      onChange={() => toggleRow(p.id)}
                      aria-label={`Select ${p.title}`}
                    />
                  </td>
                  <td className={TD_CLS}>
                    <span className="font-semibold">{p.title}</span>
                    {p.hidden && (
                      <span className="ml-2 bg-gold/15 text-gold border border-gold/40 rounded px-1.5 py-0.5 text-[0.7rem] uppercase tracking-[0.05em] align-middle">
                        Hidden
                      </span>
                    )}
                    {p.client_warnings && p.client_warnings.length > 0 && (
                      // Soft-warning chip for parses the plugin flagged at
                      // upload time (e.g. folder_hint_mismatch). The hard
                      // tamper signals are NOT here — those landed in the
                      // tamper-reports table and never reached encounters.
                      // Title tooltip shows the codes for admins who want
                      // the detail; the chip itself stays compact.
                      <span
                        className="ml-2 bg-warning/15 text-warning border border-warning/40 rounded px-1.5 py-0.5 text-[0.7rem] uppercase tracking-[0.05em] align-middle"
                        title={`Client warnings: ${p.client_warnings.join(', ')}`}
                        aria-label={`Client warnings: ${p.client_warnings.join(', ')}`}
                      >
                        ⚠ {p.client_warnings.length}
                      </span>
                    )}
                  </td>
                  <td className={`${TD_CLS} text-text-muted`}>{p.zone ?? '—'}</td>
                  <td className={`${TD_CLS} text-text-muted`}>{p.guild_name ?? '—'}</td>
                  <td className={`${TD_CLS} text-text-muted`}>{p.uploaded_by ?? '—'}</td>
                  <td className={`${TD_CLS} text-text-muted whitespace-nowrap`}>{fmtLocalDate(p.started_at)}</td>
                  <td className={`${TD_CLS} text-center text-text-muted`}>{p.player_count}</td>
                  <td className={`${TD_CLS} text-text-muted`}>{resultLabel(p.success_level)}</td>
                  <td className={`${TD_CLS} whitespace-nowrap`}>
                    <Button variant="danger" size="sm" onClick={() => purgeOne(p)} disabled={busy}>
                      Purge
                    </Button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
