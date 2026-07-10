import type { Fmt } from '../characterSheet'
import { fmtDelta } from './diff'

/**
 * Signed delta value with the compare page's colour semantics:
 * positive → success, negative → danger, zero → muted '=', null → muted '—'.
 */
export default function DeltaChip({ delta, fmt }: { delta: number | null; fmt?: Fmt }) {
  if (delta === null) return <span className="text-text-muted opacity-50">—</span>
  if (delta === 0) return <span className="text-text-muted opacity-60">=</span>
  const positive = delta > 0
  return (
    <span
      className="font-medium tabular-nums"
      style={{ color: positive ? 'var(--color-success)' : 'var(--color-danger)' }}
    >
      {fmtDelta(delta, fmt)}
    </span>
  )
}
