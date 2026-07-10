/**
 * useFavorite — favourite/unfavourite state for one character.
 *
 * Loads `{count, favorited_by_me}` on mount, then exposes an optimistic
 * `toggle()`: the star flips and the count adjusts immediately, the PUT/DELETE
 * fires in the background, and the server's response reconciles the state (or
 * a failure rolls it back and surfaces the error — e.g. the 50-per-server
 * cap's 409 message).
 */
import { useEffect, useState } from 'react'
import { useFetch } from './useFetch'
import { handle } from '../lib/api'
import { toErrorMessage } from '../lib/errors'

export interface FavoriteStatus {
  count: number
  favorited_by_me: boolean
}

export interface UseFavoriteResult {
  status: FavoriteStatus | null
  pending: boolean
  error: string | null
  toggle: () => void
}

export function useFavorite(name: string): UseFavoriteResult {
  const url = `/api/character/${encodeURIComponent(name)}/favorite`
  const { data: initial } = useFetch<FavoriteStatus>(url)
  const [status, setStatus] = useState<FavoriteStatus | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Character pages remount on navigation (App.tsx keys routes by pathname),
  // but don't depend on that remote detail: reset local state whenever the
  // target character changes so a reused mount can never mutate the wrong
  // character's favourite.
  useEffect(() => {
    setStatus(null)
    setError(null)
  }, [url])

  // Mirror the initial fetch into local state (local state wins after toggles).
  useEffect(() => {
    if (initial !== null) setStatus(prev => prev ?? initial)
  }, [initial])

  const toggle = () => {
    if (pending || status === null) return
    const prev = status
    const next: FavoriteStatus = {
      favorited_by_me: !prev.favorited_by_me,
      count: Math.max(0, prev.count + (prev.favorited_by_me ? -1 : 1)),
    }
    setStatus(next) // optimistic
    setError(null)
    setPending(true)
    fetch(url, { method: prev.favorited_by_me ? 'DELETE' : 'PUT', credentials: 'include' })
      .then(r => handle<FavoriteStatus>(r))
      .then(server => setStatus(server)) // reconcile
      .catch(err => {
        setStatus(prev) // rollback
        setError(toErrorMessage(err))
      })
      .finally(() => setPending(false))
  }

  return { status, pending, error, toggle }
}
