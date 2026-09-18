/**
 * "Census data from N ago" + a manual refresh button (logged-in users).
 *
 * POST queues into the server's paced refresh queue (one global worker, so
 * Census never gets stampeded no matter how many people click); the queued
 * state comes from GET — polled while a job is pending — so the spinner
 * survives page reloads. When the refresh lands, the SSE record stream the
 * pages already subscribe to swaps the fresh data in.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { fmtRelative } from '../formatters'
import { useAuth } from '../hooks/useAuth'

interface RefreshStatus {
  queued: boolean
  running: boolean
  last_updated: number | null
}

export function CensusRefreshControl({ kind, name }: { kind: 'character' | 'guild'; name: string }) {
  const auth = useAuth()
  const [status, setStatus] = useState<RefreshStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)
  const base = `/api/${kind}/${encodeURIComponent(name)}/refresh`

  const stopPolling = () => {
    if (timer.current) {
      clearInterval(timer.current)
      timer.current = null
    }
  }

  const poll = useCallback(async () => {
    try {
      const res = await fetch(base, { credentials: 'include' })
      if (!res.ok) return
      const s = (await res.json()) as RefreshStatus
      setStatus(s)
      if (!s.queued) stopPolling()
    } catch {
      // transient — the next poll retries
    }
  }, [base])

  useEffect(() => {
    setStatus(null)
    setError(null)
    stopPolling()
    void poll()
    return stopPolling
  }, [poll])

  // While a job is queued/running, keep polling so the spinner clears the
  // moment the worker finishes.
  useEffect(() => {
    if (status?.queued && !timer.current) timer.current = setInterval(() => void poll(), 4000)
  }, [status?.queued, poll])

  async function requestRefresh() {
    setError(null)
    try {
      const res = await fetch(base, { method: 'POST', credentials: 'include' })
      if (!res.ok) {
        setError((await res.json().catch(() => ({}))).detail ?? `Error ${res.status}`)
        return
      }
      setStatus(s => ({ queued: true, running: false, last_updated: s?.last_updated ?? null }))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  if (!status) return null
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[0.75rem] text-text-muted">
      <span>
        Census data:{' '}
        {status.last_updated != null ? `updated ${fmtRelative(status.last_updated)}` : 'never fetched'}
      </span>
      {auth.status === 'authenticated' &&
        (status.queued ? (
          <span className="text-gold inline-flex items-center gap-1">
            <span className="inline-block animate-spin" aria-hidden>
              ⟳
            </span>
            Queued for refresh
          </span>
        ) : (
          <button
            type="button"
            onClick={requestRefresh}
            className="appearance-none border-0 bg-transparent p-0 cursor-pointer text-gold-dim hover:text-gold underline decoration-dotted underline-offset-2"
          >
            ↻ Refresh
          </button>
        ))}
      {error && <span className="text-danger">{error}</span>}
    </div>
  )
}
