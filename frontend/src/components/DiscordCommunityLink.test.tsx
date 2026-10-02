import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

import type { ActiveServer } from '../hooks/useServer'

let serverState: Partial<ActiveServer> | null = null
vi.mock('../hooks/useServer', () => ({
  useServer: () => serverState,
}))

import { DiscordCommunityLink, DISCORD_COMMUNITY_LABEL } from './DiscordCommunityLink'

beforeEach(() => { serverState = null })

describe('DiscordCommunityLink', () => {
  it('renders nothing while the bootstrap is loading or when no invite is set', () => {
    const a = render(<DiscordCommunityLink variant="header" />)
    expect(a.container.firstChild).toBeNull()
    a.unmount()
    serverState = { discordInviteUrl: null }
    const b = render(<DiscordCommunityLink variant="button" />)
    expect(b.container.firstChild).toBeNull()
  })

  it('renders a new-tab icon link with the label as tooltip in the header variant', () => {
    serverState = { discordInviteUrl: 'https://discord.gg/abc123' }
    render(<DiscordCommunityLink variant="header" />)
    const link = screen.getByRole('link', { name: DISCORD_COMMUNITY_LABEL })
    expect(link).toHaveAttribute('href', 'https://discord.gg/abc123')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
    expect(link).toHaveAttribute('title', DISCORD_COMMUNITY_LABEL)
    // Icon-only: the label lives in title/aria-label, not visible text.
    expect(link.textContent).toBe('')
    expect(link.querySelector('svg')).not.toBeNull()
  })

  it('renders the Discord-coloured button in the button variant', () => {
    serverState = { discordInviteUrl: 'https://discord.com/invite/xyz' }
    render(<DiscordCommunityLink variant="button" />)
    const link = screen.getByRole('link', { name: DISCORD_COMMUNITY_LABEL })
    expect(link).toHaveAttribute('href', 'https://discord.com/invite/xyz')
    expect(link.className).toContain('bg-discord')
  })
})
