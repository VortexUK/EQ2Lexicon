/**
 * TamperReportsTable — admin view of the audit channel for parses the
 * plugin refused to send to the leaderboard.
 *
 * Populated by POST /api/parses/tamper-report (the plugin fires this
 * fire-and-forget when its heuristics block an upload). The admin sees
 * the working set (pending review) by default — switching to "ack" or
 * "all" lets them revisit older reports.
 *
 * Hard tamper signals only — the soft `client_warnings` flags that
 * ride along with successful uploads (e.g. folder_hint_mismatch) are
 * surfaced via the ⚠ chip on ParsesAdminTable instead.
 *
 * Reason codes (server contract — see CLAUDE.md "/api/parses/tamper-report"):
 *   - title_enemy_mismatch    → rename detected            → danger badge
 *   - stale_encounter         → EndTime > 1h ago           → warning badge
 *   - recent_import_activity  → Import UI was active       → warning badge
 *   - any future code         → renders verbatim + muted   → safe to add server-side later
 */
import { useCallback, useEffect, useState } from 'react'
import { Button } from '../../components/ui'
import { Badge } from '../../components/ui/Badge'
import { fmtLocalDateTime } from '../../formatters'
import {
  type TamperReport,
  type TamperReportFilter,
  type TamperReportListResponse,
  SECTION_TITLE_CLS,
  TABLE_CLS,
  TD_CLS,
  TH_CLS,
  TAMPER_REASON_LABEL,
  TAMPER_REASON_VARIANT,
} from './types'

export function TamperReportsTable() {
  const [rows, setRows] = useState<TamperReport[]>([])
  const [pending, setPending] = useState(0)
  const [filter, setFilter] = useState<TamperReportFilter>('pending')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setError(null)
    try {
      const url = `/api/admin/tamper-reports?status=${filter}`
      const res = await fetch(url, { credentials: 'include', signal })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError(`Error: ${body.detail ?? 'Failed to load tamper reports'}`)
        return
      }
      const data: TamperReportListResponse = await res.json()
      setRows(data.results)
      setPending(data.pending_count)
      setSelected(new Set())
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return
      setError('Network error — could not load tamper reports.')
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
    return () => controller.abort()
  }, [load])

  // Selection is pending-rows-only — acknowledged reports have nothing to bulk-act on.
  const pendingRows = rows.filter(r => r.acknowledged_at === null)
  const allPendingSelected = pendingRows.length > 0 && pendingRows.every(r => selected.has(r.id))

  function toggleRow(id: number) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAllPending() {
    setSelected(allPendingSelected ? new Set() : new Set(pendingRows.map(r => r.id)))
  }

  async function acknowledgeSelected() {
    const ids = [...selected]
    if (ids.length === 0) return
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/admin/tamper-reports/acknowledge-batch', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError(`Error: ${body.detail ?? 'Bulk acknowledge failed'}`)
        return
      }
      await load()
    } catch {
      setError('Network error — bulk acknowledge failed.')
    } finally {
      setBusy(false)
    }
  }

  async function acknowledge(report: TamperReport) {
    if (report.acknowledged_at !== null) return // already acked, button shouldn't render
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`/api/admin/tamper-reports/${report.id}/acknowledge`, {
        method: 'POST',
        credentials: 'include',
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setError(`Error: ${body.detail ?? 'Acknowledge failed'}`)
        return
      }
      // Reload so the row drops out of the pending filter (or moves to ack).
      await load()
    } catch {
      setError('Network error — acknowledge failed.')
    } finally {
      setBusy(false)
    }
  }

  function renderReasonBadge(reason: string) {
    const variant = TAMPER_REASON_VARIANT[reason] ?? 'muted'
    const label = TAMPER_REASON_LABEL[reason] ?? reason
    return <Badge variant={variant}>{label}</Badge>
  }

  return (
    <div>
      <div className="flex items-baseline gap-3 mb-3 flex-wrap">
        <p className={`${SECTION_TITLE_CLS} mb-0`}>
          Tamper reports ({rows.length})
        </p>
        {pending > 0 && (
          // Always show pending count so the admin sees the working-set
          // size even when they're viewing the "ack" or "all" filter.
          <Badge variant="danger">{pending} pending</Badge>
        )}
      </div>

      {/* Filter chips — pending is the default working set; "ack" /
          "all" let the admin revisit older reports. Implemented as
          buttons (not a select) so the active state is visible and
          one click cycles. */}
      <div className="flex gap-2 mb-3 flex-wrap items-center">
        {(['pending', 'ack', 'all'] as TamperReportFilter[]).map(opt => (
          <Button
            key={opt}
            variant={filter === opt ? 'primary' : 'secondary'}
            size="sm"
            type="button"
            onClick={() => setFilter(opt)}
            disabled={busy}
          >
            {opt === 'pending' ? 'Pending' : opt === 'ack' ? 'Acknowledged' : 'All'}
          </Button>
        ))}
        <Button
          variant="secondary"
          size="sm"
          type="button"
          onClick={acknowledgeSelected}
          disabled={busy || selected.size === 0}
          className="ml-auto"
        >
          Acknowledge selected ({selected.size})
        </Button>
      </div>

      {error && <p className="text-danger mb-2">{error}</p>}

      <div className="overflow-x-auto border border-border rounded-md">
        <table className={TABLE_CLS}>
          <thead>
            <tr className="bg-white/2">
              <th className={`${TH_CLS} w-[1%]`}>
                <input
                  type="checkbox"
                  checked={allPendingSelected}
                  onChange={toggleAllPending}
                  aria-label="Select all pending reports"
                  disabled={pendingRows.length === 0}
                />
              </th>
              <th className={TH_CLS}>Reason</th>
              <th className={TH_CLS}>Encounter</th>
              <th className={TH_CLS}>Zone</th>
              <th className={TH_CLS}>Server</th>
              <th className={TH_CLS}>Uploader</th>
              <th className={TH_CLS}>Reported</th>
              <th className={TH_CLS}>Status</th>
              <th className={TH_CLS}></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={9} className={`${TD_CLS} text-text-muted text-center p-6`}>
                  Loading…
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={9} className={`${TD_CLS} text-text-muted text-center p-6`}>
                  {filter === 'pending'
                    ? 'No pending tamper reports. ✓'
                    : 'No tamper reports match this filter.'}
                </td>
              </tr>
            ) : (
              rows.map(r => (
                <tr key={r.id}>
                  <td className={TD_CLS}>
                    {r.acknowledged_at === null && (
                      <input
                        type="checkbox"
                        checked={selected.has(r.id)}
                        onChange={() => toggleRow(r.id)}
                        aria-label={`Select report ${r.title}`}
                      />
                    )}
                  </td>
                  <td className={TD_CLS}>{renderReasonBadge(r.reason)}</td>
                  <td className={TD_CLS}>
                    <span className="font-semibold">{r.title}</span>
                    <span className="text-text-muted ml-2 text-[0.75rem]">
                      ({r.duration_s}s)
                    </span>
                  </td>
                  <td className={`${TD_CLS} text-text-muted`}>{r.zone ?? '—'}</td>
                  <td className={`${TD_CLS} text-text-muted`}>{r.world}</td>
                  <td className={`${TD_CLS} text-text-muted`}>
                    <div>{r.uploader_logger_name || '—'}</div>
                    {r.uploader_discord_name && (
                      <div className="text-[0.72rem]">
                        Discord: {r.uploader_discord_name}
                      </div>
                    )}
                  </td>
                  <td className={`${TD_CLS} text-text-muted whitespace-nowrap`}>
                    {fmtLocalDateTime(r.reported_at)}
                  </td>
                  <td className={TD_CLS}>
                    {r.acknowledged_at === null ? (
                      <Badge variant="warning">Pending</Badge>
                    ) : (
                      <Badge variant="muted">Acknowledged</Badge>
                    )}
                  </td>
                  <td className={`${TD_CLS} whitespace-nowrap`}>
                    {r.acknowledged_at === null && (
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => acknowledge(r)}
                        disabled={busy}
                      >
                        Acknowledge
                      </Button>
                    )}
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
