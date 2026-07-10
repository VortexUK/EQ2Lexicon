import { Link, useNavigate } from 'react-router-dom'
import { useClasses } from '../useClasses'

/**
 * Compact clickable character card used by the home page's "My Characters"
 * and "Favourites" grids. Extracted verbatim from HomePage's private
 * CharacterCard — visuals are identical; data arrives as plain props so both
 * claims and favourites can feed it.
 *
 * `detailLoaded` distinguishes "class data still fetching" (loading…) from
 * "loaded but unknown" (—) so favourites whose character record is missing
 * render name-only instead of a permanent loading state.
 */
export default function CharacterSummaryCard({ name, guildName, cls, level, tsClass, tsLevel, isPrimary = false, detailLoaded }: {
  name: string
  guildName: string | null
  cls: string | null
  level: number | null
  tsClass: string | null
  tsLevel: number | null
  isPrimary?: boolean
  detailLoaded: boolean
}) {
  const { colourFor } = useClasses()
  const accentColour = colourFor(cls, 'var(--gold)')
  const navigate = useNavigate()

  return (
    // Use a div + onClick instead of <Link> so the guild <Link> inside is not a nested <a>
    <div
      role="link"
      tabIndex={0}
      onClick={() => navigate(`/character/${encodeURIComponent(name)}`)}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') navigate(`/character/${encodeURIComponent(name)}`) }}
      className="no-underline block cursor-pointer"
    >
      <div
        className="relative border border-border rounded-md pt-[1.1rem] pr-[1.2rem] pb-[1.1rem] pl-[1.15rem] cursor-pointer overflow-hidden [transition:border-color_0.15s,background_0.15s]"
        style={{
          background: 'var(--surface)',
          borderLeft: `3px solid ${accentColour}`,
        }}
        onMouseEnter={e => {
          ;(e.currentTarget as HTMLDivElement).style.borderColor = accentColour
          ;(e.currentTarget as HTMLDivElement).style.background = 'var(--surface-raised)'
        }}
        onMouseLeave={e => {
          ;(e.currentTarget as HTMLDivElement).style.borderColor = 'var(--border)'
          ;(e.currentTarget as HTMLDivElement).style.background = 'var(--surface)'
          ;(e.currentTarget as HTMLDivElement).style.borderLeftColor = accentColour
        }}
      >

        {/* Primary star */}
        {isPrimary && (
          <span className="absolute top-[0.7rem] right-[0.8rem] text-[0.7rem] text-gold opacity-80 tracking-[0.04em]">
            ★ Primary
          </span>
        )}

        {/* Character name */}
        <div
          className="font-heading text-[1.25rem] font-bold tracking-[0.04em] leading-[1.15] mb-1"
          style={{
            color: isPrimary ? 'var(--rarity-legendary)' : 'var(--rarity-treasured)',
            textShadow: isPrimary
              ? '0 0 10px rgba(213,105,0,0.4)'
              : '0 0 8px rgba(0,120,180,0.35)',
          }}
        >
          {name}
        </div>

        {/* Guild */}
        <div className="text-[0.8rem] text-text-muted font-heading tracking-[0.03em] mb-[0.8rem] min-h-[1em]">
          {guildName
            ? <span onClick={e => { e.preventDefault(); e.stopPropagation() }}>
                <Link
                  to={`/guild/${encodeURIComponent(guildName)}`}
                  className="text-gold/70 no-underline"
                  onClick={e => e.stopPropagation()}
                >
                  &lt;{guildName}&gt;
                </Link>
              </span>
            : <span className="opacity-40">&lt;No guild&gt;</span>
          }
        </div>

        {/* Stats row */}
        <div className="flex flex-col gap-1">
          {/* Adventure class */}
          <div className="flex items-baseline gap-2">
            {detailLoaded ? (
              <span
                className="text-[0.92rem] font-semibold"
                style={{
                  color: cls ? colourFor(cls, 'var(--text)') : 'var(--text-muted)',
                }}
              >
                {cls ?? '—'}
                {level != null && (
                  <span className="font-normal text-text-muted text-[0.82rem] ml-[0.35rem]">
                    ({level})
                  </span>
                )}
              </span>
            ) : (
              <span className="text-[0.82rem] text-text-muted opacity-50">loading…</span>
            )}
          </div>

          {/* Tradeskill class */}
          <div className="flex items-baseline gap-2">
            {detailLoaded ? (
              <span className="text-[0.88rem] text-text-muted font-medium">
                {tsClass
                  ? `${tsClass.charAt(0).toUpperCase()}${tsClass.slice(1)}`
                  : '—'
                }
                {tsLevel != null && (
                  <span className="font-normal text-[0.82rem] ml-[0.35rem]">
                    ({tsLevel})
                  </span>
                )}
              </span>
            ) : (
              <span className="text-[0.82rem] text-text-muted opacity-50">loading…</span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
