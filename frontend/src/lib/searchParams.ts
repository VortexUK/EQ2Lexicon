/**
 * URL search-param helpers shared across pages that mirror tab/filter state to
 * the URL for deep-linking. React state stays the source of truth; the URL is a
 * best-effort mirror (see safeSetParams).
 */

// safeSetParams — call setSearchParams() defensively. Firefox's per-Document
// history-API quota can be depleted by browser extensions (e.g. ClearURLs
// rewriting URLs via webRequest hooks) or by Firefox's own privacy/tracking-
// protection internals running in a sandbox-eval context. When the quota is
// empty, react-router's setSearchParams call throws DOMException SecurityError
// and silently fails. We catch + retry-after-quota-reset so the URL eventually
// catches up to React state. State is the source of truth; URL sync is
// best-effort — if it never lands, the page still works fine.
export function safeSetParams(setParams: (...args: unknown[]) => void, args: unknown[]): void {
  try {
    setParams(...args)
  } catch (e) {
    if (e instanceof DOMException && (e.name === 'SecurityError' || e.name === 'InvalidStateError')) {
      // Firefox's throttle window is ~10s. Retry after 1.2s in case quota recovers fast.
      // If it still fails, give up — React state is correct, URL is just slightly stale.
      setTimeout(() => {
        try {
          setParams(...args)
        } catch {
          /* give up silently — page still works, URL is stale */
        }
      }, 1200)
    } else {
      throw e
    }
  }
}

/**
 * Functional updater for setSearchParams that MERGES the given key/values into
 * the current params (rather than replacing the whole query). A null/empty
 * value deletes the key. Lets sibling components each own a subset of the
 * params without clobbering each other's.
 *
 * Usage: safeSetParams(setSearchParams, [mergeParams({ tab: 'aas' }), { replace: true }])
 */
export function mergeParams(
  updates: Record<string, string | null | undefined>,
): (prev: URLSearchParams) => URLSearchParams {
  return (prev) => {
    const next = new URLSearchParams(prev)
    for (const [key, value] of Object.entries(updates)) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    return next
  }
}
