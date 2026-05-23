import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { useClaim } from '../hooks/useClaim'
import type { Claim } from '../hooks/useClaim'
import ServerLaunchTimer from '../components/ServerLaunchTimer'

// ── Class colours (adventure archetype) ──────────────────────────────────────

const CLASS_COLOURS: Record<string, string> = {
  // Fighters
  Guardian: '#f87171', Berserker: '#f87171',
  Paladin: '#f87171', Shadowknight: '#f87171',
  Monk: '#f87171', Bruiser: '#f87171',
  // Scouts
  Ranger: '#fbbf24', Assassin: '#fbbf24',
  Troubador: '#fbbf24', Dirge: '#fbbf24',
  Swashbuckler: '#fbbf24', Brigand: '#fbbf24',
  // Mages
  Wizard: '#93b4ff', Warlock: '#93b4ff',
  Conjuror: '#93b4ff', Necromancer: '#93b4ff',
  Illusionist: '#93b4ff', Coercer: '#93b4ff',
  // Priests
  Templar: '#4ade80', Inquisitor: '#4ade80',
  Mystic: '#4ade80', Defiler: '#4ade80',
  Warden: '#4ade80', Fury: '#4ade80',
}

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
  const accentColour = detail?.cls ? (CLASS_COLOURS[detail.cls] ?? '#c8a96e') : '#c8a96e'
  const navigate = useNavigate()

  return (
    // Use a div + onClick instead of <Link> so the guild <Link> inside is not a nested <a>
    <div
      role="link"
      tabIndex={0}
      onClick={() => navigate(`/character/${encodeURIComponent(claim.character_name)}`)}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') navigate(`/character/${encodeURIComponent(claim.character_name)}`) }}
      style={{ textDecoration: 'none', display: 'block', cursor: 'pointer' }}
    >
      <div style={{
        position: 'relative',
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderLeft: `3px solid ${accentColour}`,
        borderRadius: 8,
        padding: '1.1rem 1.2rem 1.1rem 1.15rem',
        cursor: 'pointer',
        transition: 'border-color 0.15s, background 0.15s',
        overflow: 'hidden',
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
          <span style={{
            position: 'absolute', top: '0.7rem', right: '0.8rem',
            fontSize: '0.7rem', color: '#c8a96e', opacity: 0.8,
            letterSpacing: '0.04em',
          }}>
            ★ Primary
          </span>
        )}

        {/* Character name */}
        <div style={{
          fontFamily: "'Cinzel', serif",
          fontSize: '1.25rem',
          fontWeight: 700,
          letterSpacing: '0.04em',
          lineHeight: 1.15,
          marginBottom: '0.2rem',
          color: isPrimary ? '#ffc993' : '#93d9ff',
          textShadow: isPrimary
            ? '0 0 10px rgba(213,105,0,0.4)'
            : '0 0 8px rgba(0,120,180,0.35)',
        }}>
          {claim.character_name}
        </div>

        {/* Guild */}
        <div style={{
          fontSize: '0.8rem',
          color: 'var(--text-muted)',
          fontFamily: "'Cinzel', serif",
          letterSpacing: '0.03em',
          marginBottom: '0.8rem',
          minHeight: '1em',
        }}>
          {claim.guild_name
            ? <span onClick={e => { e.preventDefault(); e.stopPropagation() }}>
                <Link
                  to={`/guild/${encodeURIComponent(claim.guild_name)}`}
                  style={{ color: 'rgba(200,169,110,0.7)', textDecoration: 'none' }}
                  onClick={e => e.stopPropagation()}
                >
                  &lt;{claim.guild_name}&gt;
                </Link>
              </span>
            : <span style={{ opacity: 0.4 }}>&lt;No guild&gt;</span>
          }
        </div>

        {/* Stats row */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.28rem' }}>
          {/* Adventure */}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem' }}>
            <span style={{
              fontSize: '0.67rem', textTransform: 'uppercase', letterSpacing: '0.07em',
              color: 'var(--text-muted)', width: 68, flexShrink: 0,
            }}>
              Adventure
            </span>
            {detail ? (
              <span style={{
                fontSize: '0.92rem', fontWeight: 600,
                color: detail.cls ? (CLASS_COLOURS[detail.cls] ?? 'var(--text)') : 'var(--text-muted)',
              }}>
                {detail.cls ?? '—'}
                {detail.level != null && (
                  <span style={{ fontWeight: 400, color: 'var(--text-muted)', fontSize: '0.82rem', marginLeft: '0.35rem' }}>
                    ({detail.level})
                  </span>
                )}
              </span>
            ) : (
              <span style={{ fontSize: '0.82rem', color: 'var(--text-muted)', opacity: 0.5 }}>loading…</span>
            )}
          </div>

          {/* Tradeskill */}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem' }}>
            <span style={{
              fontSize: '0.67rem', textTransform: 'uppercase', letterSpacing: '0.07em',
              color: 'var(--text-muted)', width: 68, flexShrink: 0,
            }}>
              Tradeskill
            </span>
            {detail ? (
              <span style={{ fontSize: '0.88rem', color: 'var(--text-muted)', fontWeight: 500 }}>
                {detail.ts_class
                  ? `${detail.ts_class.charAt(0).toUpperCase()}${detail.ts_class.slice(1)}`
                  : '—'
                }
                {detail.ts_level != null && (
                  <span style={{ fontWeight: 400, fontSize: '0.82rem', marginLeft: '0.35rem' }}>
                    ({detail.ts_level})
                  </span>
                )}
              </span>
            ) : (
              <span style={{ fontSize: '0.82rem', color: 'var(--text-muted)', opacity: 0.5 }}>loading…</span>
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
    <aside style={{
      width: 210,
      flexShrink: 0,
    }}>
      <h2 style={{
        fontFamily: "'Cinzel', serif",
        fontSize: '0.88rem',
        fontWeight: 600,
        letterSpacing: '0.1em',
        textTransform: 'uppercase',
        color: 'rgba(200,169,110,0.7)',
        margin: '0 0 0.85rem',
      }}>
        My Guilds
      </h2>

      <div style={{
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        overflow: 'hidden',
      }}>
        {guilds.map(([name, count], i) => (
          <Link
            key={name}
            to={`/guild/${encodeURIComponent(name)}`}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '0.6rem 0.85rem',
              borderBottom: i < guilds.length - 1 ? '1px solid var(--border)' : 'none',
              textDecoration: 'none',
              transition: 'background 0.1s',
            }}
            onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface-raised)')}
            onMouseLeave={e => (e.currentTarget.style.background = '')}
          >
            <span style={{
              fontFamily: "'Cinzel', serif",
              fontSize: '0.82rem',
              color: '#c8a96e',
              fontWeight: 500,
              letterSpacing: '0.02em',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              flex: 1,
              marginRight: '0.5rem',
            }}>
              {name}
            </span>
            <span style={{
              fontSize: '0.72rem',
              color: 'var(--text-muted)',
              background: 'var(--surface-raised)',
              border: '1px solid var(--border)',
              borderRadius: 10,
              padding: '0.05rem 0.45rem',
              flexShrink: 0,
            }}>
              {count}
            </span>
          </Link>
        ))}
      </div>
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
    <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', textAlign: 'center', marginTop: '2rem' }}>
      Loading…
    </p>
  )
  if (claimState.status === 'unauthenticated' || claimState.status === 'error') return null

  const { approved, pending } = claimState.data
  const primary = approved.find(c => c.is_primary === 1) ?? approved[0] ?? null

  if (approved.length === 0 && !pending) {
    return (
      <div style={{ textAlign: 'center', marginTop: '3rem', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.75rem' }}>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.95rem' }}>
          You haven't claimed a character yet.
        </p>
        <Link
          to="/claim"
          style={{
            display: 'inline-block',
            padding: '0.5rem 1.4rem',
            background: 'rgba(var(--accent-rgb,99,210,130),0.12)',
            color: 'var(--accent)',
            border: '1px solid rgba(var(--accent-rgb,99,210,130),0.35)',
            borderRadius: 6,
            textDecoration: 'none',
            fontSize: '0.9rem',
            fontWeight: 600,
          }}
        >
          Claim a character
        </Link>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', gap: '2rem', alignItems: 'flex-start' }}>

      {/* Left: character cards */}
      <div style={{ flex: 1, minWidth: 0 }}>

        {/* Section header */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.75rem', marginBottom: '1.1rem' }}>
          <h2 style={{
            fontFamily: "'Cinzel', serif",
            fontSize: '0.88rem',
            fontWeight: 600,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            color: 'rgba(200,169,110,0.7)',
            margin: 0,
          }}>
            My Characters
          </h2>
          <Link to="/claim" style={{ color: 'var(--text-muted)', fontSize: '0.78rem', textDecoration: 'none' }}>
            manage
          </Link>
        </div>

        {/* Cards grid */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))',
          gap: '0.85rem',
        }}>
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
          <div style={{
            marginTop: '1rem',
            display: 'flex', alignItems: 'center', gap: '0.5rem',
            fontSize: '0.83rem', color: 'var(--text-muted)',
          }}>
            <span>⏳</span>
            <span style={{ fontStyle: 'italic' }}>{pending.character_name}</span>
            <Link to="/claim" style={{ color: 'var(--text-muted)', textDecoration: 'none' }}>· pending approval</Link>
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
    <main style={{ maxWidth: 1100, margin: '0 auto', padding: '0.5rem 1.5rem 4rem' }}>

      {/* Hero */}
      <div style={{ textAlign: 'center', marginBottom: '2rem' }}>

        {/* Logo image — dark edges blend into page background */}
        <div style={{ position: 'relative', display: 'inline-block', lineHeight: 0 }}>
          <img
            src="/logo.png"
            alt="EQ2 Lexicon"
            style={{
              width: '100%',
              maxWidth: 520,
              display: 'block',
              margin: '0 auto',
              // Mask the bottom edge so it dissolves into the page rather than cutting hard
              WebkitMaskImage: 'linear-gradient(to bottom, black 60%, transparent 100%)',
              maskImage:        'linear-gradient(to bottom, black 60%, transparent 100%)',
            }}
          />
        </div>

        <p style={{
          color: 'var(--text-muted)', fontSize: '0.95rem', lineHeight: 1.6,
          marginTop: '-0.5rem',  // pull up slightly under the faded bottom of the image
        }}>
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
