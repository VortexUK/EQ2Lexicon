import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useNotifications } from '../hooks/useNotifications'

// ── Bell SVG (Feather-style, 16×16 viewport) ──────────────────────────────────

function BellIcon() {
  return (
    <svg
      width="15" height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </svg>
  )
}

// ── Row inside the dropdown ───────────────────────────────────────────────────

function NotifRow({
  count,
  label,
  sublabel,
  accentColor,
  accentBg,
  accentBorder,
  hasBorder,
  onClick,
}: {
  count: number
  label: string
  sublabel?: string
  accentColor: string
  accentBg: string
  accentBorder: string
  hasBorder: boolean
  onClick: () => void
}) {
  const [hovered, setHovered] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.65rem',
        width: '100%',
        padding: '0.65rem 1rem',
        background: hovered ? 'var(--surface)' : 'transparent',
        border: 'none',
        borderBottom: hasBorder ? '1px solid var(--border)' : 'none',
        color: 'var(--text)',
        cursor: 'pointer',
        textAlign: 'left',
        fontSize: '0.88rem',
        transition: 'background 0.1s',
      }}
    >
      {/* Count pill */}
      <span style={{
        background:   accentBg,
        color:        accentColor,
        border:       `1px solid ${accentBorder}`,
        borderRadius: 4,
        fontSize:     '0.68rem',
        fontWeight:   700,
        padding:      '0.15rem 0.45rem',
        minWidth:     22,
        textAlign:    'center',
        flexShrink:   0,
      }}>
        {count}
      </span>

      {/* Text */}
      <span style={{ flex: 1, lineHeight: 1.3 }}>
        {label}
        {sublabel && (
          <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem', display: 'block' }}>
            {sublabel}
          </span>
        )}
      </span>

      <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>→</span>
    </button>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function NotificationBell() {
  const data     = useNotifications()
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const ref      = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  if (!data) return null
  const total = data.pending_claims + data.pending_users
  if (total === 0) return null

  const claimsPath = data.officer_guild
    ? `/guild/${encodeURIComponent(data.officer_guild)}`
    : '/admin'

  function go(path: string) {
    setOpen(false)
    navigate(path)
  }

  const bothRows = data.pending_claims > 0 && data.pending_users > 0

  return (
    <div ref={ref} style={{ position: 'relative' }}>

      {/* Trigger button */}
      <button
        onClick={() => setOpen(v => !v)}
        title="Pending notifications"
        style={{
          display:      'flex',
          alignItems:   'center',
          gap:          '0.35rem',
          background:   'none',
          border:       '1px solid rgba(200,169,110,0.35)',
          borderRadius: 6,
          padding:      '0.3rem 0.55rem',
          cursor:       'pointer',
          color:        '#c8a96e',
          fontSize:     '0.82rem',
          lineHeight:   1,
          transition:   'border-color 0.15s, color 0.15s',
        }}
        onMouseEnter={e => {
          e.currentTarget.style.borderColor = 'rgba(200,169,110,0.7)'
          e.currentTarget.style.color = '#e8d5a3'
        }}
        onMouseLeave={e => {
          e.currentTarget.style.borderColor = 'rgba(200,169,110,0.35)'
          e.currentTarget.style.color = '#c8a96e'
        }}
      >
        <BellIcon />
        <span style={{
          background:   '#ef4444',
          color:        '#fff',
          fontSize:     '0.65rem',
          fontWeight:   700,
          lineHeight:   1,
          padding:      '0.12rem 0.32rem',
          borderRadius: 10,
          minWidth:     16,
          textAlign:    'center',
        }}>
          {total}
        </span>
      </button>

      {/* Dropdown */}
      {open && (
        <div style={{
          position:   'absolute',
          right:      0,
          top:        'calc(100% + 6px)',
          background: 'var(--surface-raised)',
          border:     '1px solid var(--border)',
          borderRadius: 8,
          minWidth:   240,
          zIndex:     300,
          boxShadow:  '0 8px 28px rgba(0,0,0,0.5)',
          overflow:   'hidden',
        }}>

          {/* Header */}
          <div style={{
            padding:       '0.45rem 1rem',
            fontSize:      '0.66rem',
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
            color:         'var(--text-muted)',
            borderBottom:  '1px solid var(--border)',
            fontFamily:    "'Cinzel', serif",
          }}>
            Needs Attention
          </div>

          {data.pending_claims > 0 && (
            <NotifRow
              count={data.pending_claims}
              label={`Pending claim${data.pending_claims !== 1 ? 's' : ''}`}
              sublabel={data.officer_guild ?? undefined}
              accentColor="#fbbf24"
              accentBg="rgba(234,179,8,0.15)"
              accentBorder="rgba(234,179,8,0.4)"
              hasBorder={bothRows}
              onClick={() => go(claimsPath)}
            />
          )}

          {data.pending_users > 0 && (
            <NotifRow
              count={data.pending_users}
              label={`Pending user${data.pending_users !== 1 ? 's' : ''}`}
              sublabel="awaiting access"
              accentColor="#f87171"
              accentBg="rgba(239,68,68,0.15)"
              accentBorder="rgba(239,68,68,0.4)"
              hasBorder={false}
              onClick={() => go('/admin')}
            />
          )}
        </div>
      )}
    </div>
  )
}
