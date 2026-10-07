import { SupporterBadge, useSupporters } from './SupporterBadge'

/**
 * Renders a parse uploader as `Alice 👑 · Menludiir` (Discord name + badge,
 * then the logging character). Parses with no Discord identity show just the
 * character name; a display name equal to the character name (ignoring case)
 * renders once.
 */
export function UploaderTag({
  characterName,
  discordId,
  displayName,
  /** Override the muted "as <character>" prefix wording. Defaults to "·". */
  separator = '·',
}: {
  characterName: string
  discordId: string | null | undefined
  displayName: string | null | undefined
  separator?: string
}) {
  const supporters = useSupporters()
  const isSupporter = !!discordId && supporters.has(discordId)

  // No Discord identity at all (pre-plugin / local upload).
  if (!discordId || !displayName) {
    return (
      <>
        {characterName}
        {isSupporter && <SupporterBadge />}
      </>
    )
  }

  // Discord display name and character name are effectively the same —
  // don't duplicate. (Trimming for the comparison only; the rendered
  // value uses the original casing.)
  if (displayName.trim().toLowerCase() === characterName.trim().toLowerCase()) {
    return (
      <>
        {displayName}
        {isSupporter && <SupporterBadge />}
      </>
    )
  }

  return (
    <>
      {displayName}
      {isSupporter && <SupporterBadge />}
      <span className="text-text-muted ml-1">
        {separator} {characterName}
      </span>
    </>
  )
}
