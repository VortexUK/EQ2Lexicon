import { useCensusStream } from '../hooks/useCensusStream'

/**
 * Unobtrusive inline badge shown when a page is serving stale stored data.
 *
 * Three honest states: Census is down (showing stored data), a background
 * refresh is actually running (`refreshing` — the server only sets it when
 * the throttle/health gate let one start, and the SSE "no change" event
 * clears it), or simply stored data with nothing in flight. It used to say
 * "Updating from Census…" for every stale record, forever, including the
 * common case where Census has nothing newer for a character who hasn't
 * logged in recently.
 */
export function FreshnessBadge({ stale, refreshing }: { stale: boolean | undefined; refreshing?: boolean }) {
  const { health } = useCensusStream()
  if (!stale) return null
  const down = health === 'down'
  const label = down
    ? 'Census unavailable — showing stored data'
    : refreshing
      ? 'Updating from Census…'
      : 'Showing stored data'
  return <span className="text-[0.72rem] text-text-muted italic">{label}</span>
}
