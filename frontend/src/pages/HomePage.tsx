import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { useClaim } from '../hooks/useClaim'
import type { Claim } from '../hooks/useClaim'
import ServerLaunchTimer from '../components/ServerLaunchTimer'
import { Card } from '../components/ui'
// logo.webp lives in frontend/public/ → served at site root by Vite
const logo = '/logo.webp'
import { useClasses } from '../useClasses'

// ── Character detail (fetched from Census cache) ──────────────────────────────

interface CharDetail {
  cls: string | null
  level: number | null
  ts_class: string | null
  ts_level: number | null
}

// ── Character card ────────────────────────────────────────────────────────────

function CharacterCard({ claim, detail, isPrimary }: {
  claim: Claim
  detail: CharDetail | null
  isPrimary: boolean
}) {
  const { colourFor } = useClasses()
  const accentColour = colourFor(detail?.cls, 'var(--gold)')
  const navigate = useNavigate()

  return (
    // Use a div + onClick instead of <Link> so the guild <Link> inside is not a nested <a>
    <div
      role="link"
      tabIndex={0}
      onClick={() => navigate(`/character/${encodeURIComponent(claim.character_name)}`)}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') navigate(`/character/${encodeURIComponent(claim.character_name)}`) }}
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
          {claim.character_name}
        </div>

        {/* Guild */}
        <div className="text-[0.8rem] text-text-muted font-heading tracking-[0.03em] mb-[0.8rem] min-h-[1em]">
          {claim.guild_name
            ? <span onClick={e => { e.preventDefault(); e.stopPropagation() }}>
                <Link
                  to={`/guild/${encodeURIComponent(claim.guild_name)}`}
                  className="text-gold/70 no-underline"
                  onClick={e => e.stopPropagation()}
                >
                  &lt;{claim.guild_name}&gt;
                </Link>
              </span>
            : <span className="opacity-40">&lt;No guild&gt;</span>
          }
        </div>

        {/* Stats row */}
        <div className="flex flex-col gap-1">
          {/* Adventure class */}
          <div className="flex items-baseline gap-2">
            {detail ? (
              <span
                className="text-[0.92rem] font-semibold"
                style={{
                  color: detail.cls ? colourFor(detail.cls, 'var(--text)') : 'var(--text-muted)',
                }}
              >
                {detail.cls ?? '—'}
                {detail.level != null && (
                  <span className="font-normal text-text-muted text-[0.82rem] ml-[0.35rem]">
                    ({detail.level})
                  </span>
                )}
              </span>
            ) : (
              <span className="text-[0.82rem] text-text-muted opacity-50">loading…</span>
            )}
          </div>

          {/* Tradeskill class */}
          <div className="flex items-baseline gap-2">
            {detail ? (
              <span className="text-[0.88rem] text-text-muted font-medium">
                {detail.ts_class
                  ? `${detail.ts_class.charAt(0).toUpperCase()}${detail.ts_class.slice(1)}`
                  : '—'
                }
                {detail.ts_level != null && (
                  <span className="font-normal text-[0.82rem] ml-[0.35rem]">
                    ({detail.ts_level})
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

// ── Guilds sidebar ────────────────────────────────────────────────────────────

function GuildsSidebar({ approved }: { approved: Claim[] }) {
  // Collect unique guilds with a count of how many of the user's chars are in each
  const guildMap = new Map<string, number>()
  for (const c of approved) {
    if (c.guild_name) {
      guildMap.set(c.guild_name, (guildMap.get(c.guild_name) ?? 0) + 1)
    }
  }
  const guilds = [...guildMap.entries()].sort(([a], [b]) => a.localeCompare(b))

  if (guilds.length === 0) return null

  return (
    <aside className="w-full md:w-[210px] md:shrink-0">
      <h2 className="font-heading text-[0.88rem] font-semibold tracking-[0.1em] uppercase text-gold/70 mt-0 mx-0 mb-3">
        My Guilds
      </h2>

      <Card className="p-0 overflow-hidden">
        {guilds.map(([name, count], i) => (
          <Link
            key={name}
            to={`/guild/${encodeURIComponent(name)}`}
            className="flex items-center justify-between py-2.5 px-3 no-underline [transition:background_0.1s]"
            style={{
              borderBottom: i < guilds.length - 1 ? '1px solid var(--border)' : 'none',
            }}
            onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface-raised)')}
            onMouseLeave={e => (e.currentTarget.style.background = '')}
          >
            <span className="font-heading text-[0.82rem] text-gold font-medium tracking-[0.02em] overflow-hidden text-ellipsis whitespace-nowrap flex-1 mr-2">
              {name}
            </span>
            <span className="text-[0.72rem] text-text-muted bg-surface-raised border border-border rounded-full py-px px-2 shrink-0">
              {count}
            </span>
          </Link>
        ))}
      </Card>
    </aside>
  )
}

// ── My Characters grid ────────────────────────────────────────────────────────

function MyCharacters() {
  const claimState = useClaim()
  const [details, setDetails] = useState<Record<string, CharDetail>>({})

  useEffect(() => {
    if (claimState.status !== 'ready') return
    const approved = claimState.data.approved
    if (approved.length === 0) return

    // Fetch class/level data for all approved characters in parallel
    Promise.all(
      approved.map(c =>
        fetch(`/api/character/${encodeURIComponent(c.character_name)}`, { credentials: 'include' })
          .then(r => r.ok ? r.json() : null)
          .then(data => ({
            name: c.character_name,
            detail: data ? {
              cls: data.cls ?? null,
              level: data.level ?? null,
              ts_class: data.ts_class ?? null,
              ts_level: data.ts_level ?? null,
            } : null,
          }))
          .catch(() => ({ name: c.character_name, detail: null }))
      )
    ).then(results => {
      const map: Record<string, CharDetail> = {}
      for (const r of results) {
        if (r.detail) map[r.name] = r.detail
      }
      setDetails(map)
    })
  }, [claimState.status === 'ready' ? claimState.data.approved.map(c => c.character_name).join(',') : ''])

  if (claimState.status === 'loading') return (
    <p className="text-text-muted text-[0.9rem] text-center mt-8">
      Loading…
    </p>
  )
  if (claimState.status === 'unauthenticated' || claimState.status === 'error') return null

  const { approved, pending } = claimState.data
  const primary = approved.find(c => c.is_primary === 1) ?? approved[0] ?? null

  if (approved.length === 0 && !pending) {
    return (
      <div className="text-center mt-12 flex flex-col items-center gap-3">
        <p className="text-text-muted text-[0.95rem]">
          You haven't claimed a character yet.
        </p>
        <Link
          to="/claim"
          className="inline-block py-2 px-[1.4rem] text-gold rounded-md no-underline text-[0.9rem] font-semibold"
          style={{
            background: 'rgba(var(--accent-rgb),0.12)',
            border: '1px solid rgba(var(--accent-rgb),0.35)',
          }}
        >
          Claim a character
        </Link>
      </div>
    )
  }

  return (
    <div className="flex flex-col-reverse md:flex-row gap-8 md:items-start">

      {/* Left: character cards */}
      <div className="flex-1 min-w-0">

        {/* Section header */}
        <div className="flex items-baseline gap-3 mb-[1.1rem]">
          <h2 className="font-heading text-[0.88rem] font-semibold tracking-[0.1em] uppercase text-gold/70 m-0">
            My Characters
          </h2>
          <Link to="/claim" className="text-text-muted text-[0.78rem] no-underline">
            manage
          </Link>
        </div>

        {/* Cards grid */}
        <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-3">
          {approved.map(c => (
            <CharacterCard
              key={c.id}
              claim={c}
              detail={details[c.character_name] ?? null}
              isPrimary={c === primary}
            />
          ))}
        </div>

        {/* Pending claim notice */}
        {pending && (
          <div className="mt-4 flex items-center gap-2 text-[0.83rem] text-text-muted">
            <span>⏳</span>
            <span className="italic">{pending.character_name}</span>
            <Link to="/claim" className="text-text-muted no-underline">· pending approval</Link>
          </div>
        )}
      </div>

      {/* Right: guilds sidebar */}
      <GuildsSidebar approved={approved} />

    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function HomePage() {
  const auth = useAuth()

  return (
    <main className="max-w-[1100px] mx-auto pt-2 px-6 pb-16">

      {/* Hero */}
      <div className="text-center mb-8">

        {/* Logo image — dark edges blend into page background */}
        <div className="relative inline-block leading-none">
          <img
            src={logo}
            alt="EQ2 Lexicon"
            className="w-full max-w-[520px] block mx-auto"
            style={{
              // Mask the bottom edge so it dissolves into the page rather than cutting hard
              WebkitMaskImage: 'linear-gradient(to bottom, black 60%, transparent 100%)',
              maskImage:        'linear-gradient(to bottom, black 60%, transparent 100%)',
            }}
          />
        </div>

        {/* pull up slightly under the faded bottom of the image */}
        <p className="text-text-muted text-[0.95rem] leading-[1.6] -mt-2">
          EverQuest 2 companion — track characters, spells,
          gear and guild rosters across the realm of Norrath.
        </p>
        <ServerLaunchTimer />
      </div>

      {/* Characters */}
      {auth.status === 'authenticated' && <MyCharacters />}

    </main>
  )
}
