import type { ReactNode } from 'react'

/**
 * DiscordButton — the standard Discord-blurple link button. Used by the
 * login gate, the user widget when signed out, the claim flow, and the
 * "Join our Discord community" call to action.
 * The ONLY place the Discord colour token is used.
 */
interface DiscordButtonProps {
  href?: string
  children?: ReactNode
  /** Pass target="_blank" (with rel) for links that leave the site. */
  target?: string
  rel?: string
}

export function DiscordButton({
  href = '/api/auth/login',
  children = 'Sign in with Discord',
  target,
  rel,
}: DiscordButtonProps) {
  return (
    <a
      href={href}
      target={target}
      rel={rel}
      className={[
        'inline-block no-underline rounded-md',
        'px-4 py-2 text-[0.95rem] font-semibold tracking-[0.02em]',
        'bg-discord text-white',
        'hover:brightness-110 transition-[filter] duration-150',
      ].join(' ')}
    >
      {children}
    </a>
  )
}
