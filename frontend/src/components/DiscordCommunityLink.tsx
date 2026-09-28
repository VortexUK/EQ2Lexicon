/**
 * DiscordCommunityLink — the "Join our Discord community" link, driven by
 * the admin-set site setting delivered in the /api/server bootstrap.
 * Renders nothing at all when no invite is configured, so the footer and
 * Support page read exactly as before in that case.
 */
import { DiscordButton } from './ui/DiscordButton'
import { useServer } from '../hooks/useServer'

export const DISCORD_COMMUNITY_LABEL = 'Join our Discord community'

const FOOTER_LINK_CLS = 'text-[color:inherit] underline underline-offset-[3px] inline-block py-1 -my-1'

export function DiscordCommunityLink({ variant }: { variant: 'footer' | 'button' }) {
  const url = useServer()?.discordInviteUrl
  if (!url) return null
  if (variant === 'footer') {
    return (
      <span>
        <a href={url} target="_blank" rel="noopener noreferrer" className={FOOTER_LINK_CLS}>
          {DISCORD_COMMUNITY_LABEL}
        </a>
      </span>
    )
  }
  return (
    <DiscordButton href={url} target="_blank" rel="noopener noreferrer">
      {DISCORD_COMMUNITY_LABEL}
    </DiscordButton>
  )
}
