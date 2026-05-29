import { useCensusStream } from '../hooks/useCensusStream'

/**
 * Small Census-API health indicator shown in the site footer.
 * Renders a coloured dot + label: green (online) when health is 'up' or
 * 'unknown' (don't alarm the user before the first probe lands), red
 * (offline) only on a confirmed 'down'.
 */
export default function CensusStatus() {
  const { health } = useCensusStream()
  const isDown = health === 'down'

  const label   = isDown ? 'Census offline' : 'Census online'
  const tooltip = isDown
    ? 'Census unavailable — showing stored data'
    : 'Census online'

  // Dot colour is data-driven → inline style is appropriate here per project
  // Tailwind v4 rules. The glow reinforces visibility on the dark footer.
  const dotColor = isDown ? 'var(--color-danger)' : 'var(--color-success)'
  const dotGlow  = isDown
    ? '0 0 5px rgba(248,113,113,0.7)'
    : '0 0 5px rgba(74,222,128,0.7)'

  return (
    <span
      className="inline-flex items-center gap-1.5"
      title={tooltip}
    >
      <span
        className="inline-block rounded-full shrink-0"
        style={{
          width:     '8px',
          height:    '8px',
          background: dotColor,
          boxShadow:  dotGlow,
        }}
        aria-hidden="true"
      />
      <span>{label}</span>
    </span>
  )
}
