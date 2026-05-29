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
      className="flex items-center gap-2.5 w-full py-2.5 px-4 border-none text-text cursor-pointer text-left text-[0.88rem] transition-colors"
      style={{
        background: hovered ? 'var(--surface)' : 'transparent',
        borderBottom: hasBorder ? '1px solid var(--border)' : 'none',
      }}
    >
      {/* Count pill */}
      <span
        className="rounded-sm text-[0.68rem] font-bold py-0.5 px-2 min-w-[22px] text-center shrink-0"
        style={{
          background: accentBg,
          color:      accentColor,
          border:     `1px solid ${accentBorder}`,
        }}
      >
        {count}
      </span>

      {/* Text */}
      <span className="flex-1 leading-[1.3]">
        {label}
        {sublabel && (
          <span className="text-text-muted text-[0.78rem] block">
            {sublabel}
          </span>
        )}
      </span>

      <span className="text-text-muted text-[0.75rem]">→</span>
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
    <div ref={ref} className="relative">

      {/* Trigger button */}
      <button
        onClick={() => setOpen(v => !v)}
        title="Pending notifications"
        className="flex items-center gap-1.5 bg-none rounded-sm2 py-1 px-2 cursor-pointer text-gold text-[0.82rem] leading-none transition-colors"
        style={{ border: '1px solid rgba(var(--gold-rgb), 0.35)' }}
        onMouseEnter={e => {
          e.currentTarget.style.borderColor = 'rgba(var(--gold-rgb), 0.7)'
          e.currentTarget.style.color = '#e8d5a3'
        }}
        onMouseLeave={e => {
          e.currentTarget.style.borderColor = 'rgba(var(--gold-rgb), 0.35)'
          e.currentTarget.style.color = 'var(--gold)'
        }}
      >
        <BellIcon />
        <span
          className="text-[0.65rem] font-bold leading-none py-0.5 px-1.5 rounded-full min-w-[16px] text-center bg-danger text-white"
        >
          {total}
        </span>
      </button>

      {/* Dropdown */}
      {open && (
        <div
          className="absolute right-0 top-[calc(100%+6px)] bg-surface-raised border border-border rounded-md min-w-[240px] z-dropdown overflow-hidden"
          style={{ boxShadow: '0 8px 24px rgba(0,0,0,0.4)' }}
        >

          {/* Header */}
          <div className="py-2 px-4 text-[0.66rem] uppercase tracking-[0.08em] text-text-muted border-b border-border font-heading">
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
              accentColor="var(--danger)"
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
