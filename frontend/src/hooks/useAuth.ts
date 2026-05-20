import { useEffect, useState } from 'react'

export interface User {
  id: string
  username: string
  global_name: string | null
  avatar: string | null
  is_admin: boolean
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
        if (data) setState({ status: 'authenticated', user: data as User })
      })
      .catch(() => setState({ status: 'unauthenticated' }))
  }, [])

  return state
}

export function avatarUrl(user: User): string {
  if (user.avatar) {
    return `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png`
  }
  // Default Discord avatar based on discriminator bucket
  const index = Number(BigInt(user.id) >> 22n) % 6
  return `https://cdn.discordapp.com/embed/avatars/${index}.png`
}
