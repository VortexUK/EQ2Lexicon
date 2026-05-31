import { useEffect, useState } from 'react'

export interface User {
  id: string
  username: string
  global_name: string | null
  avatar: string | null
  is_admin: boolean
  access_status: 'approved' | 'pending' | 'denied'
  /**
   * DB-granted roles (currently `'contributor'`). Excludes `'admin'`
   * (exposed separately via `is_admin`) and `'officer'` (dynamic,
   * computed server-side and not exposed here to avoid a Census round-
   * trip on every page load). Frontend uses this to show the Edit
   * affordance to contributors.
   */
  static_roles: string[]
}

export type AuthState =
  | { status: 'loading' }
  | { status: 'authenticated'; user: User }
  | { status: 'unauthenticated' }

export function useAuth(): AuthState {
  const [state, setState] = useState<AuthState>({ status: 'loading' })

  useEffect(() => {
    fetch('/api/auth/me', { credentials: 'include' })
      .then(res => {
        if (res.status === 401) {
          setState({ status: 'unauthenticated' })
          return null
        }
        return res.json()
      })
      .then(data => {
        if (isUser(data)) {
          setState({ status: 'authenticated', user: data })
        } else {
          setState({ status: 'unauthenticated' })
        }
      })
      .catch(() => setState({ status: 'unauthenticated' }))
  }, [])

  return state
}

/**
 * Build a Discord CDN avatar URL from a user id + avatar hash.
 * Falls back to the default avatar bucket when avatar is null.
 * Used directly by components that receive (id, avatar) separately
 * (e.g. admin and guild claim lists).
 */
export function discordAvatarUrl(id: string, avatar: string | null): string {
  if (avatar) return `https://cdn.discordapp.com/avatars/${id}/${avatar}.png`
  const index = Number(BigInt(id) >> 22n) % 6
  return `https://cdn.discordapp.com/embed/avatars/${index}.png`
}

/** Convenience wrapper for a full User object. */
export function avatarUrl(user: User): string {
  return discordAvatarUrl(user.id, user.avatar)
}

/** Runtime type guard — narrows `unknown` SSE/API data to `User`. */
export function isUser(data: unknown): data is User {
  if (!data || typeof data !== 'object') return false
  const d = data as Record<string, unknown>
  return typeof d.id === 'string' &&
         typeof d.is_admin === 'boolean' &&
         Array.isArray(d.static_roles)
}

/** True if the authed user is either an admin or has the 'contributor' role.
 *  Used to gate the Edit buttons on raid strategy / triggers / boss editor. */
export function isContributor(auth: AuthState): boolean {
  return auth.status === 'authenticated' &&
    (auth.user.is_admin || auth.user.static_roles.includes('contributor'))
}

/** True if the authed user is an admin (env-driven, see ADMIN_DISCORD_IDS).
 *  Stricter than `isContributor` — used to gate admin-only affordances like
 *  the featured raid expansion / zone curation UI on /raids. */
export function isAdmin(auth: AuthState): boolean {
  return auth.status === 'authenticated' && auth.user.is_admin
}
