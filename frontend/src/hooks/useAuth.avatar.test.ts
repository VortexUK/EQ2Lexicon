// Regression: discordAvatarUrl must NEVER throw — BigInt(undefined) in the
// default-avatar fallback once blanked the entire guild claims tab when the
// API response lacked discord_id and the claimant had no custom avatar.
import { describe, expect, it } from 'vitest'
import { discordAvatarUrl } from './useAuth'

describe('discordAvatarUrl', () => {
  it('builds the custom-avatar URL from id + hash', () => {
    expect(discordAvatarUrl('648253204760625160', 'abc123')).toBe(
      'https://cdn.discordapp.com/avatars/648253204760625160/abc123.png',
    )
  })

  it('derives the default bucket from the snowflake when there is no avatar', () => {
    const url = discordAvatarUrl('648253204760625160', null)
    expect(url).toMatch(/^https:\/\/cdn\.discordapp\.com\/embed\/avatars\/[0-5]\.png$/)
  })

  it('degrades to bucket 0 instead of throwing on a missing id', () => {
    expect(discordAvatarUrl(undefined, null)).toBe('https://cdn.discordapp.com/embed/avatars/0.png')
  })

  it('degrades to bucket 0 instead of throwing on a non-numeric id', () => {
    expect(discordAvatarUrl('not-a-snowflake', null)).toBe('https://cdn.discordapp.com/embed/avatars/0.png')
  })

  it('a hash without an id still cannot produce a broken avatars/undefined URL', () => {
    expect(discordAvatarUrl(undefined, 'abc123')).toBe('https://cdn.discordapp.com/embed/avatars/0.png')
  })
})
