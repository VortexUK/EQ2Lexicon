import { useCallback, useEffect, useState } from 'react'

export interface Claim {
  id: number
  discord_id: string
  character_name: string
  status: 'pending' | 'approved' | 'rejected' | 'withdrawn' | 'superseded'
  requested_at: number
  reviewed_at: number | null
  note: string | null
  is_primary: number   // 1 = primary, 0 = alt
}

export interface ClaimsData {
  approved: Claim[]
  pending: Claim | null
}

export type ClaimState =
  | { status: 'loading' }
  | { status: 'unauthenticated' }
  | { status: 'ready'; data: ClaimsData }
  | { status: 'error' }

export function useClaim(): ClaimState & { refetch: () => void } {
  const [state, setState] = useState<ClaimState>({ status: 'loading' })
  const [tick, setTick] = useState(0)

  useEffect(() => {
    setState({ status: 'loading' })
    fetch('/api/claim/me', { credentials: 'include' })
      .then(async res => {
        if (res.status === 401) { setState({ status: 'unauthenticated' }); return }
        if (!res.ok) { setState({ status: 'error' }); return }
        const data: ClaimsData = await res.json()
        setState({ status: 'ready', data })
      })
      .catch(() => setState({ status: 'error' }))
  }, [tick])

  const refetch = useCallback(() => setTick(t => t + 1), [])

  return { ...state, refetch }
}
