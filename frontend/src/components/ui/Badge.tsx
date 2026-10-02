/**
 * Badge — small rounded label with semantic colour.
 *
 * Replaces the ad-hoc badge styling in AdminPage (ACCESS_BADGE, CLAIM_BADGE),
 * GuildPage (item-watch status), ClaimPage (claim status), NotificationBell
 * (unread count). One styling source, four call sites.
 */
import type { ReactNode } from 'react'

type Variant = 'success' | 'warning' | 'danger' | 'info' | 'muted' | 'gold'

const VARIANT_CLASSES: Record<Variant, string> = {
  success: 'bg-[rgba(var(--success-rgb),0.18)] border-[rgba(var(--success-rgb),0.4)] text-success',
  warning: 'bg-[rgba(var(--warning-rgb),0.18)] border-[rgba(var(--warning-rgb),0.4)] text-warning',
  danger:  'bg-[rgba(var(--danger-rgb),0.18)]  border-[rgba(var(--danger-rgb),0.4)]  text-danger',
  info:    'bg-surface-raised border-border text-text',
  muted:   'bg-surface-raised border-border text-text-muted',
  gold:    'bg-[rgba(var(--gold-rgb),0.15)] border-[rgba(var(--gold-rgb),0.4)] text-gold',
}

interface BadgeProps {
  variant?: Variant
  children: ReactNode
  className?: string
  /** Native tooltip text (hover explanation for compact badges). */
  title?: string
}

export function Badge({ variant = 'muted', children, className = '', title }: BadgeProps) {
  const cls = `inline-block rounded-sm px-2 py-[2px] text-[0.72rem] font-semibold whitespace-nowrap border ${VARIANT_CLASSES[variant]} ${className}`
  return (
    <span className={cls} title={title}>
      {children}
    </span>
  )
}
