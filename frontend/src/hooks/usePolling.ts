import { useEffect, useRef } from 'react'

export interface UsePollingOptions {
  /** Poll only while true (e.g. gate on a session). Default true. */
  enabled?: boolean
  /** Fraction of the interval applied as ±random offset to every tick. Default 0.2. */
  jitter?: number
  /** Run once right away on start (or on first becoming visible). Default true. */
  immediate?: boolean
}

/**
 * Call `fn` every `intervalMs` (±jitter) while the tab is visible.
 *
 * - Pauses while `document.visibilityState === 'hidden'`; on becoming visible
 *   again it runs once immediately if a tick came due in the meantime.
 * - Every interval is jittered so tabs don't phase-align after a deploy.
 * - Never overlaps: a tick is skipped while the previous `fn` promise is pending.
 * - Cleared on unmount. Errors from `fn` are swallowed (callers keep stale data).
 */
export function usePolling(
  fn: () => void | Promise<unknown>,
  intervalMs: number,
  { enabled = true, jitter = 0.2, immediate = true }: UsePollingOptions = {},
): void {
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    if (!enabled) return

    let timer: ReturnType<typeof setTimeout> | null = null
    let dueAt = 0
    let pending = false
    let disposed = false

    const nextDelay = () => Math.max(0, intervalMs * (1 + (Math.random() * 2 - 1) * jitter))
    const isHidden = () => document.visibilityState === 'hidden'

    function run() {
      if (pending) return
      pending = true
      Promise.resolve()
        .then(() => fnRef.current())
        .catch(() => { /* keep last known data */ })
        .finally(() => { pending = false })
    }

    function arm() {
      if (timer !== null) clearTimeout(timer)
      timer = null
      if (disposed || isHidden()) return
      timer = setTimeout(tick, Math.max(0, dueAt - Date.now()))
    }

    function tick() {
      timer = null
      run()
      dueAt = Date.now() + nextDelay()
      arm()
    }

    function onVisibility() {
      if (isHidden()) {
        if (timer !== null) clearTimeout(timer)
        timer = null
      } else {
        arm() // a missed tick has dueAt <= now, so this fires immediately
      }
    }

    dueAt = immediate ? Date.now() : Date.now() + nextDelay()
    arm()
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      disposed = true
      if (timer !== null) clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [intervalMs, enabled, jitter, immediate])
}
