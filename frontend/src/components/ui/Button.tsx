import type { ButtonHTMLAttributes, AnchorHTMLAttributes, ReactNode } from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'icon'

function classes(variant: Variant, size: Size, extra?: string): string {
  const sizeCls = size === 'sm' ? 'btn--sm' : size === 'icon' ? 'btn--icon' : ''
  return ['btn', `btn--${variant}`, sizeCls, extra]
    .filter(Boolean)
    .join(' ')
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant
  size?: Size
  children: ReactNode
}

/**
 * Shared button. Styling lives in the `.btn` / `.btn--*` classes in
 * index.css so hover/focus/disabled states work. Pass `style` for one-off
 * layout tweaks (margins, width) — not colours.
 */
export function Button({ variant = 'secondary', size = 'md', className, children, ...rest }: ButtonProps) {
  return (
    <button className={classes(variant, size, className)} {...rest}>
      {children}
    </button>
  )
}

type LinkButtonProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  variant?: Variant
  size?: Size
  children: ReactNode
}

/** Anchor styled as a button (for real navigations / external links). */
export function LinkButton({ variant = 'secondary', size = 'md', className, children, ...rest }: LinkButtonProps) {
  return (
    <a className={classes(variant, size, className)} {...rest}>
      {children}
    </a>
  )
}
