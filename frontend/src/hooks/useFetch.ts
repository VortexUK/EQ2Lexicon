/**
 * useFetch (auto, refetch on url change) / useLazyFetch (caller-triggered run()).
 * Always sends `credentials: 'include'`; aborts in-flight requests on url change
 * or unmount (AbortError is swallowed). Non-2xx → Error(`detail` ?? `HTTP {status}`).
 */
import { useEffect, useRef, useState, useCallback } from 'react'

export interface UseFetchResult<T> {
  data: T | null
  loading: boolean
  error: string | null
  /** HTTP status of the last response, or null if no response arrived (network error / abort / never fetched). */
  statusCode: number | null
  refetch: () => void
}

export interface UseFetchOptions {
  /** Skip auto-fetch on mount and on url change. Useful when url is null
   *  while gate conditions are pending. */
  enabled?: boolean
  /** Optional transform applied to the parsed JSON before setData. */
  select?: (raw: unknown) => unknown
  /** Optional fetch init (method, headers, body). credentials is forced. */
  init?: RequestInit
}

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json()
    if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
      return body.detail
    }
  } catch { /* not JSON */ }
  return `HTTP ${res.status}`
}

export function useFetch<T>(url: string | null, opts: UseFetchOptions = {}): UseFetchResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(opts.enabled !== false && !!url)
  const [error, setError] = useState<string | null>(null)
  const [statusCode, setStatusCode] = useState<number | null>(null)
  const [tick, setTick] = useState(0)
  const enabled = opts.enabled !== false
  // Hold the select/init in refs so the effect doesn't re-fire on every render
  // when the caller passes a fresh inline function/object.
  const selectRef = useRef(opts.select)
  selectRef.current = opts.select
  const initRef = useRef(opts.init)
  initRef.current = opts.init

  useEffect(() => {
    if (!enabled || !url) {
      setLoading(false)
      setStatusCode(null)
      return
    }
    const ctrl = new AbortController()
    setLoading(true)
    setError(null)
    setStatusCode(null)
    fetch(url, { credentials: 'include', signal: ctrl.signal, ...(initRef.current ?? {}) })
      .then(async res => {
        if (!ctrl.signal.aborted) setStatusCode(res.status)
        if (!res.ok) throw new Error(await readError(res))
        const raw = await res.json()
        const next = selectRef.current ? selectRef.current(raw) : raw
        if (!ctrl.signal.aborted) setData(next as T)
      })
      .catch(err => {
        if (ctrl.signal.aborted) return
        if (err instanceof Error && err.name === 'AbortError') return
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false)
      })
    return () => ctrl.abort()
  }, [url, enabled, tick])

  const refetch = useCallback(() => setTick(t => t + 1), [])
  return { data, loading, error, statusCode, refetch }
}

/**
 * useLazyFetch — for tab-triggered or button-triggered fetches.
 *
 * Returns `{ data, loading, error, run, reset }`. The caller invokes `run()`
 * with a fetch URL when the user opens the tab; subsequent renders don't
 * re-fetch.
 */
export interface UseLazyFetchResult<T> {
  data: T | null
  loading: boolean
  error: string | null
  /** HTTP status of the last response, or null if no response arrived (network error / abort / never fetched). */
  statusCode: number | null
  /** Trigger the fetch. Safe to call multiple times — replaces in-flight request. */
  run: (url: string, init?: RequestInit) => void
  /** Clear state (data + error + statusCode). Loading is untouched. */
  reset: () => void
}

export function useLazyFetch<T>(): UseLazyFetchResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [statusCode, setStatusCode] = useState<number | null>(null)
  const ctrlRef = useRef<AbortController | null>(null)

  useEffect(() => () => ctrlRef.current?.abort(), [])

  const run = useCallback((url: string, init?: RequestInit) => {
    ctrlRef.current?.abort()
    const ctrl = new AbortController()
    ctrlRef.current = ctrl
    setLoading(true)
    setError(null)
    setStatusCode(null)
    fetch(url, { credentials: 'include', signal: ctrl.signal, ...(init ?? {}) })
      .then(async res => {
        if (!ctrl.signal.aborted) setStatusCode(res.status)
        if (!res.ok) throw new Error(await readError(res))
        const raw = await res.json()
        if (!ctrl.signal.aborted) setData(raw as T)
      })
      .catch(err => {
        if (ctrl.signal.aborted) return
        if (err instanceof Error && err.name === 'AbortError') return
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false)
      })
  }, [])

  const reset = useCallback(() => {
    setData(null)
    setError(null)
    setStatusCode(null)
  }, [])

  return { data, loading, error, statusCode, run, reset }
}
