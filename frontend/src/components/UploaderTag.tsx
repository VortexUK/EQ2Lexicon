import { SupporterBadge, useSupporters } from './SupporterBadge'

/**
 * Renders a parse uploader's identity for inline display:
 *
 *     Alice 👑 · Menludiir
 *
 * The Discord display name takes priority because the supporter badge
 * sits next to it; the character name shows after a separator as the
 * "logged from" context (whose POV the parse captures).
 *
 * Backward compatibility — older parses uploaded without the plugin
 * (or pre-v0.1.10 plugin builds) carry no Discord identity. In those
 * cases the display falls back to JUST the character name with no
 * badge: `Menludiir`.
 *
 * Single-name collapse — if the resolved display name is the same as
 * the character name (or differs only in case), we don't render both;
 * just the one. Catches the "discord username == character name" case
 * that's surprisingly common in this community.
 *
 * Props are kept loose (string | null | undefined) because the parse
 * response model permits all three for the Discord-side fields.
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
