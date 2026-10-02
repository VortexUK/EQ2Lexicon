/**
 * DiscordCommunityLink — the "Join our Discord community" link, driven by
 * the admin-set site setting delivered in the /api/server bootstrap.
 * Renders nothing at all when no invite is configured.
 *
 * Variants: `header` is the icon-only button in the fixed header's top-right
 * cluster (gold like its neighbours — house rule keeps Discord blurple for
 * the sign-in button only), with the label as a native hover tooltip;
 * `button` is the full DiscordButton on the Support page.
 */
import { DiscordButton } from './ui/DiscordButton'
import { useServer } from '../hooks/useServer'

export const DISCORD_COMMUNITY_LABEL = 'Join our Discord community'

// The Discord mark (official brand SVG path), drawn with currentColor so it
// inherits the header's gold instead of brand blurple.
function DiscordIcon() {
  return (
    <svg width="16" height="12" viewBox="0 0 127.14 96.36" fill="currentColor" aria-hidden="true">
      <path d="M107.7 8.07A105.15 105.15 0 0 0 81.47 0a72.06 72.06 0 0 0-3.36 6.83 97.68 97.68 0 0 0-29.11 0A72.37 72.37 0 0 0 45.64 0a105.89 105.89 0 0 0-26.25 8.09C2.79 32.65-1.71 56.6.54 80.21a105.73 105.73 0 0 0 32.17 16.15 77.7 77.7 0 0 0 6.89-11.11 68.42 68.42 0 0 1-10.85-5.18c.91-.66 1.8-1.34 2.66-2a75.57 75.57 0 0 0 64.32 0c.87.71 1.76 1.39 2.66 2a68.68 68.68 0 0 1-10.87 5.19 77 77 0 0 0 6.89 11.1 105.25 105.25 0 0 0 32.19-16.14c2.64-27.38-4.51-51.11-18.9-72.15ZM42.45 65.69C36.18 65.69 31 60 31 53s5-12.74 11.43-12.74S54 46 53.89 53s-5.05 12.69-11.44 12.69Zm42.24 0C78.41 65.69 73.25 60 73.25 53s5-12.74 11.44-12.74S96.23 46 96.12 53s-5.04 12.69-11.43 12.69Z" />
    </svg>
  )
}

export function DiscordCommunityLink({ variant }: { variant: 'header' | 'button' }) {
  const url = useServer()?.discordInviteUrl
  if (!url) return null
  if (variant === 'header') {
    return (
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        title={DISCORD_COMMUNITY_LABEL}
        aria-label={DISCORD_COMMUNITY_LABEL}
        className="flex items-center rounded-sm2 py-1 px-2 text-gold leading-none transition-colors border border-[rgba(var(--gold-rgb),0.35)] hover:border-[rgba(var(--gold-rgb),0.7)] hover:text-[#e8d5a3]"
      >
        <DiscordIcon />
      </a>
    )
  }
  return (
    <DiscordButton href={url} target="_blank" rel="noopener noreferrer">
      {DISCORD_COMMUNITY_LABEL}
    </DiscordButton>
  )
}
