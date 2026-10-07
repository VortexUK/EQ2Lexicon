/**
 * useSortable — sort key + direction for a tabular dataset. Strings compare
 * case-insensitively, numbers/dates numerically; null/undefined sort last in
 * both directions.
 */
import { useMemo, useState, useRef } from 'react'

export type SortDir = 'asc' | 'desc'

export interface UseSortableResult<T, K extends string> {
  sorted: T[]
  sortKey: K
  sortDir: SortDir
  handleSort: (key: K) => void
}

export function useSortable<T, K extends string>(
  rows: T[],
  getValue: (row: T, key: K) => unknown,
  initialKey: K,
  initialDir: SortDir = 'asc',
  defaultDirFor?: (key: K) => SortDir,
): UseSortableResult<T, K> {
  const [sortKey, setSortKey] = useState<K>(initialKey)
  const [sortDir, setSortDir] = useState<SortDir>(initialDir)

  // Hold defaultDirFor in a ref so callers can pass inline arrows without
  // triggering spurious re-renders (same pattern as getValueRef below).
  const defaultDirForRef = useRef(defaultDirFor)
  defaultDirForRef.current = defaultDirFor

  function handleSort(key: K) {
    if (key === sortKey) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir(defaultDirForRef.current ? defaultDirForRef.current(key) : 'asc')
    }
  }

  // Hold getValue in a ref so callers passing an inline arrow don't
  // trigger a re-sort on every render. The latest function is always used
  // when the memo does run.
  const getValueRef = useRef(getValue)
  getValueRef.current = getValue

  const sorted = useMemo(() => {
    const sign = sortDir === 'asc' ? 1 : -1
    const get = getValueRef.current
    return [...rows].sort((a, b) => {
      const va = get(a, sortKey)
      const vb = get(b, sortKey)
      if (va == null && vb == null) return 0
      if (va == null) return 1   // nulls last regardless of direction
      if (vb == null) return -1
      if (typeof va === 'string' && typeof vb === 'string') {
        return sign * va.localeCompare(vb)
      }
      if (typeof va === 'number' && typeof vb === 'number') {
        return sign * (va - vb)
      }
      return sign * String(va).localeCompare(String(vb))
    })
  }, [rows, sortKey, sortDir])

  return { sorted, sortKey, sortDir, handleSort }
}
