import type { ReactNode } from 'react'

/**
 * Shared toggle pill — the standard for on/off filter chips (rank filters, size
 * filters, status filters). Active = the same subtle gold tint + gold-bright
 * text the FilterDropdown uses for its selected rows (works whether one chip or
 * many are active); inactive = a muted bordered surface. Replaces the per-page
 * pillStyle()/BTN_BASE styling so toggles look identical everywhere. (Companion
 * to FilterDropdown, the select-one standard.)
 */
export function FilterPill({
  active,
  onClick,
  children,
  title,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
  title?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`cursor-pointer appearance-none whitespace-nowrap rounded-pill border px-3 py-1.5 text-[0.82rem] transition-colors ${
        active
          ? 'border-gold/50 bg-gold/15 font-semibold text-gold-bright'
          : 'border-border bg-surface font-medium text-text-muted hover:border-gold/40 hover:text-text'
      }`}
    >
      {children}
    </button>
  )
}
