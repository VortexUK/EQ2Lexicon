import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { useClaim } from '../hooks/useClaim'
import type { Claim } from '../hooks/useClaim'
import ServerLaunchTimer from '../components/ServerLaunchTimer'
import CharacterSummaryCard from '../components/CharacterSummaryCard'
import FavoritesSection from '../components/FavoritesSection'
import { Card } from '../components/ui'
// logo.webp lives in frontend/public/ → served at site root by Vite
const logo = '/logo.webp'

// ── Character detail (fetched from Census cache) ──────────────────────────────

interface CharDetail {
  cls: string | null
  level: number | null
  ts_class: string | null
  ts_level: number | null
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
          {approved.map(c => {
            const detail = details[c.character_name] ?? null
            return (
              <CharacterSummaryCard
                key={c.id}
                name={c.character_name}
                guildName={c.guild_name}
                cls={detail?.cls ?? null}
                level={detail?.level ?? null}
                tsClass={detail?.ts_class ?? null}
                tsLevel={detail?.ts_level ?? null}
                isPrimary={c === primary}
                detailLoaded={detail !== null}
              />
            )
          })}
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

      {/* Favourites — bookmarks, independent of claims; hidden when empty */}
      {auth.status === 'authenticated' && (
        <div className="mt-10">
          <FavoritesSection />
        </div>
      )}

    </main>
  )
}
