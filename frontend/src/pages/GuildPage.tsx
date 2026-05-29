import { useEffect, useMemo, useState } from 'react'
import { useLazyFetch } from '../hooks/useFetch'
import { useParams } from 'react-router-dom'
import Breadcrumb from '../components/Breadcrumb'
import { FilterPill } from '../components/FilterPill'
import { useClaim } from '../hooks/useClaim'
import { useAuth, discordAvatarUrl } from '../hooks/useAuth'
import { Button, Card } from '../components/ui'
import { TabButton } from '../components/ui/TabButton'
import { FreshnessBadge } from '../components/FreshnessBadge'
import { useCensusStream } from '../hooks/useCensusStream'
import { fmtLocalDate, fmtRelative } from '../formatters'
import { GuildRosterTab } from './guild/GuildRosterTab'
import { GuildSpellCheckTab } from './guild/GuildSpellCheckTab'
import { GuildAdornCheckTab } from './guild/GuildAdornCheckTab'
import type {
  GuildData,
  GuildSpellCheck,
  GuildAdornCheck,
  Tab,
} from './guild/types'
import { TH_CLS, TD_CLS } from './guild/types'

// ── Local types (not shared with sub-tabs) ───────────────────────────────────

interface GuildInfo {
  name: string
  world: string
  dateformed: number | null
  description: string | null
  alignment: string | null
  type: string | null
  level: number | null
  members: number | null
  accounts: number | null
  achievement_count: number
}

interface GuildClaimItem {
  id: number
  discord_id: string
  discord_name: string
  avatar: string | null
  character_name: string
  requested_at: number
  is_own: boolean
}

interface ItemWatchEntry {
  id: number
  character_name: string
  item_id: number
  item_name: string
  added_by_name: string
  added_at: number
  first_seen_at: number | null
  last_seen_at: number | null
  last_checked_at: number | null
}

// ── Style helpers ─────────────────────────────────────────────────────────────

// ── Guild info stat chip ──────────────────────────────────────────────────────

function InfoStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[0.68rem] uppercase tracking-[0.07em] text-text-muted">
        {label}
      </span>
      <span className="text-[0.92rem] text-text font-medium">
        {value}
      </span>
    </div>
  )
}

// ── Claim requests tab (officers only) ───────────────────────────────────────

function ClaimRequestsTab({
  guildName,
  currentDiscordId,
}: {
  guildName: string
  currentDiscordId: string
}) {
  const [claims, setClaims]     = useState<GuildClaimItem[] | null>(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [busy, setBusy]         = useState<number | null>(null)   // claim ID being actioned
  const [rejectId, setRejectId] = useState<number | null>(null)   // claim ID open for reject note
  const [rejectNote, setRejectNote] = useState('')

  useEffect(() => {
    setLoading(true)
    fetch(`/api/guild/${encodeURIComponent(guildName)}/claims`, { credentials: 'include' })
      .then(async res => {
        if (!res.ok) { setError((await res.json().catch(() => ({}))).detail ?? `Error ${res.status}`); return }
        setClaims(await res.json())
      })
      .catch(() => setError('Network error — please try again.'))
      .finally(() => setLoading(false))
  }, [guildName])

  async function handleApprove(id: number) {
    setBusy(id)
    try {
      const res = await fetch(`/api/guild/${encodeURIComponent(guildName)}/claims/${id}/approve`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      if (!res.ok) { alert((await res.json().catch(() => ({}))).detail ?? 'Failed'); return }
      setClaims(prev => prev ? prev.filter(c => c.id !== id) : prev)
    } finally { setBusy(null) }
  }

  async function handleReject(id: number, note: string) {
    setBusy(id)
    try {
      const res = await fetch(`/api/guild/${encodeURIComponent(guildName)}/claims/${id}/reject`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: note.trim() || null }),
      })
      if (!res.ok) { alert((await res.json().catch(() => ({}))).detail ?? 'Failed'); return }
      setClaims(prev => prev ? prev.filter(c => c.id !== id) : prev)
      setRejectId(null)
      setRejectNote('')
    } finally { setBusy(null) }
  }

  if (loading) return <p className="text-text-muted p-4">Loading claim requests…</p>
  if (error)   return <p className="text-danger p-4">{error}</p>
  if (!claims) return null

  if (claims.length === 0) {
    return (
      <div className="p-8 text-center text-text-muted">
        No pending claim requests for this guild.
      </div>
    )
  }

  return (
    <div className="px-4 py-3">
      {claims.map(c => {
        const isOwn    = c.discord_id === currentDiscordId
        const isBusy   = busy === c.id
        const rejecting = rejectId === c.id
        const age = Math.floor((Date.now() / 1000 - c.requested_at) / 3600)
        const ageStr = age < 1 ? 'just now' : age < 24 ? `${age}h ago` : `${Math.floor(age / 24)}d ago`

        return (
          <div key={c.id} className="flex items-start gap-3 py-3 border-b border-border">
            {/* Discord avatar */}
            <img
              src={discordAvatarUrl(c.discord_id, c.avatar)}
              alt=""
              className="w-[38px] h-[38px] rounded-full shrink-0 mt-0.5"
            />

            {/* Info */}
            <div className="flex-1 min-w-0">
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="font-semibold text-text">{c.discord_name}</span>
                <span className="text-text-muted text-[0.8rem]">is claiming</span>
                <span className="text-gold font-semibold">{c.character_name}</span>
                {isOwn && (
                  <span
                    className="text-[0.68rem] font-bold px-1.5 py-px rounded-sm text-gold uppercase tracking-[0.05em]"
                    style={{ background: 'rgba(var(--gold-rgb), 0.15)', border: '1px solid rgba(var(--gold-rgb), 0.3)' }}
                  >Your claim</span>
                )}
              </div>
              <div className="text-[0.78rem] text-text-muted mt-0.5">
                Submitted {ageStr}
              </div>

              {/* Reject note input */}
              {rejecting && (
                <div className="mt-2.5 flex gap-1.5 flex-wrap">
                  <input
                    type="text"
                    placeholder="Reason (optional)…"
                    value={rejectNote}
                    onChange={e => setRejectNote(e.target.value)}
                    className="flex-1 min-w-[160px] text-[0.85rem]"
                    autoFocus
                  />
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => handleReject(c.id, rejectNote)}
                    disabled={isBusy}
                  >
                    {isBusy ? '…' : 'Confirm reject'}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => { setRejectId(null); setRejectNote('') }}
                  >
                    Cancel
                  </Button>
                </div>
              )}
            </div>

            {/* Action buttons — hidden for own claims and while reject form is open */}
            {!isOwn && !rejecting && (
              <div className="flex gap-1.5 shrink-0">
                <button
                  onClick={() => handleApprove(c.id)}
                  disabled={isBusy}
                  className="px-3 py-1 rounded-sm cursor-pointer text-[0.85rem] font-semibold"
                  style={{
                    background: 'rgba(var(--success-rgb), 0.15)', color: 'var(--success)',
                    border: '1px solid rgba(var(--success-rgb), 0.35)',
                  }}
                >
                  {isBusy ? '…' : 'Approve'}
                </button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => { setRejectId(c.id); setRejectNote('') }}
                  disabled={isBusy}
                >
                  Reject
                </Button>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Item watch tab (officers only) ───────────────────────────────────────────

function watchStatus(w: ItemWatchEntry): { icon: string; label: string; colour: string } {
  if (w.last_checked_at === null) {
    return { icon: '⏳', label: 'Not yet checked', colour: 'var(--text-muted)' }
  }
  if (w.last_seen_at !== null && w.last_seen_at === w.last_checked_at) {
    return { icon: '🟢', label: 'Currently wearing', colour: 'var(--success)' }
  }
  if (w.last_seen_at !== null) {
    const ago = Math.floor((Date.now() / 1000 - w.last_seen_at) / 3600)
    const label = ago < 1 ? 'last seen just now' : ago < 24 ? `last seen ${ago}h ago` : `last seen ${Math.floor(ago / 24)}d ago`
    return { icon: '🟡', label, colour: '#eab308' }
  }
  return { icon: '🔴', label: 'Never seen wearing it', colour: 'var(--danger)' }
}

function ItemWatchTab({ guildName }: { guildName: string }) {
  const [watches, setWatches]   = useState<ItemWatchEntry[] | null>(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [charInput, setCharInput] = useState('')
  const [itemInput, setItemInput] = useState('')
  const [adding, setAdding]     = useState(false)
  const [addError, setAddError] = useState<string | null>(null)
  const [removing, setRemoving] = useState<number | null>(null)

  useEffect(() => {
    setLoading(true)
    fetch(`/api/guild/${encodeURIComponent(guildName)}/item-watch`, { credentials: 'include' })
      .then(async res => {
        if (!res.ok) { setError((await res.json().catch(() => ({}))).detail ?? `Error ${res.status}`); return }
        setWatches(await res.json())
      })
      .catch(() => setError('Network error — please try again.'))
      .finally(() => setLoading(false))
  }, [guildName])

  async function handleAdd() {
    const char = charInput.trim()
    const item = itemInput.trim()
    if (!char || !item) return
    setAdding(true)
    setAddError(null)
    try {
      const res = await fetch(`/api/guild/${encodeURIComponent(guildName)}/item-watch`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ character_name: char, item_name: item }),
      })
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))).detail ?? 'Failed to add watch'
        setAddError(detail)
        return
      }
      const entry: ItemWatchEntry = await res.json()
      setWatches(prev => prev ? [entry, ...prev] : [entry])
      setCharInput('')
      setItemInput('')
    } finally {
      setAdding(false)
    }
  }

  async function handleRemove(id: number) {
    setRemoving(id)
    try {
      const res = await fetch(`/api/guild/${encodeURIComponent(guildName)}/item-watch/${id}`, {
        method: 'DELETE', credentials: 'include',
      })
      if (!res.ok) { alert((await res.json().catch(() => ({}))).detail ?? 'Failed'); return }
      setWatches(prev => prev ? prev.filter(w => w.id !== id) : prev)
    } finally {
      setRemoving(null)
    }
  }

  if (loading) return <p className="text-text-muted p-4">Loading item watches…</p>
  if (error)   return <p className="text-danger p-4">{error}</p>

  return (
    <div className="px-4 py-3">

      {/* Add form */}
      <div className="flex gap-2 flex-wrap items-start mb-[1.1rem] pb-4 border-b border-border">
        <div className="flex flex-col gap-1">
          <label className="text-[0.7rem] text-text-muted uppercase tracking-[0.06em]">Character</label>
          <input
            type="text"
            placeholder="e.g. Sihtric"
            value={charInput}
            onChange={e => setCharInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleAdd()}
            className="w-[160px] text-[0.88rem]"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[0.7rem] text-text-muted uppercase tracking-[0.06em]">Item name</label>
          <input
            type="text"
            placeholder="e.g. Faded Black Hood"
            value={itemInput}
            onChange={e => setItemInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleAdd()}
            className="w-[240px] text-[0.88rem]"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[0.7rem] text-transparent select-none">_</label>
          <button
            onClick={handleAdd}
            disabled={adding || !charInput.trim() || !itemInput.trim()}
            className="px-4 py-1.5 rounded-sm2 cursor-pointer text-[0.88rem] font-semibold"
            style={{
              background: 'rgba(var(--accent-rgb),0.15)',
              color: 'var(--accent)',
              border: '1px solid rgba(var(--accent-rgb),0.35)',
              opacity: adding || !charInput.trim() || !itemInput.trim() ? 0.5 : 1,
            }}
          >
            {adding ? 'Adding…' : '+ Add Watch'}
          </button>
        </div>
        {addError && (
          <div className="w-full text-danger text-[0.83rem] mt-1">
            {addError}
          </div>
        )}
      </div>

      {/* Watch list */}
      {watches && watches.length === 0 ? (
        <div className="text-center text-text-muted py-6">
          No items being watched for this guild yet.
        </div>
      ) : (
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b-2 border-border bg-surface-raised">
              <th className={`${TH_CLS} text-text-muted text-left`}>Item</th>
              <th className={`${TH_CLS} text-text-muted text-left`}>Character</th>
              <th className={`${TH_CLS} text-text-muted text-left`}>Added by</th>
              <th className={`${TH_CLS} text-text-muted text-left`}>Added</th>
              <th className={`${TH_CLS} text-text-muted text-left`}>Status</th>
              <th className={`${TH_CLS} text-text-muted text-left w-12`}></th>
            </tr>
          </thead>
          <tbody>
            {(watches ?? []).map(w => {
              const { icon, label, colour } = watchStatus(w)
              return (
                <tr key={w.id} className="border-b border-border">
                  <td className={`${TD_CLS} font-medium text-text`}>{w.item_name}</td>
                  <td className={`${TD_CLS} text-gold`}>{w.character_name}</td>
                  <td className={`${TD_CLS} text-text-muted text-[0.82rem]`}>{w.added_by_name}</td>
                  <td className={`${TD_CLS} text-text-muted text-[0.82rem]`}>{fmtRelative(w.added_at)}</td>
                  <td className={`${TD_CLS} text-[0.85rem] whitespace-nowrap`} style={{ color: colour }}>
                    {icon} {label}
                  </td>
                  <td className={`${TD_CLS} text-right px-2 py-1`}>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleRemove(w.id)}
                      disabled={removing === w.id}
                      title="Remove watch"
                    >
                      {removing === w.id ? '…' : '✕'}
                    </Button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function GuildPage() {
  const { guildName } = useParams<{ guildName: string }>()
  const claimState = useClaim()
  const auth = useAuth()
  const { subscribe } = useCensusStream()

  const myChars = useMemo<Set<string>>(() => {
    if (claimState.status !== 'ready') return new Set()
    return new Set(claimState.data.approved.map(c => c.character_name.toLowerCase()))
  }, [claimState])

  const [isOfficer, setIsOfficer] = useState(false)

  const [tab, setTab] = useState<Tab>('roster')
  const [filter, setFilter] = useState('')
  const [hiddenRanks, setHiddenRanks] = useState<Set<string>>(new Set())

  // Guild info state
  const [info, setInfo] = useState<GuildInfo | null>(null)

  // Roster state
  const [roster, setRoster] = useState<GuildData | null>(null)
  const [rosterError, setRosterError] = useState<string | null>(null)
  const [rosterLoading, setRosterLoading] = useState(true)

  // Spell check state
  const {
    data: spells,
    loading: spellsLoading,
    error: spellsError,
    run: runSpells,
  } = useLazyFetch<GuildSpellCheck>()

  // Adorn check state
  const {
    data: adorns,
    loading: adornsLoading,
    error: adornsError,
    run: runAdorns,
  } = useLazyFetch<GuildAdornCheck>()

  // Load roster + info + officer status on mount
  useEffect(() => {
    if (!guildName) return
    setRosterLoading(true)
    setRosterError(null)

    Promise.all([
      fetch(`/api/guild/${encodeURIComponent(guildName)}`, { credentials: 'include' }),
      fetch(`/api/guild/${encodeURIComponent(guildName)}/info`, { credentials: 'include' }),
      fetch(`/api/guild/${encodeURIComponent(guildName)}/officer-status`, { credentials: 'include' }),
    ]).then(async ([rosterRes, infoRes, officerRes]) => {
      if (rosterRes.status === 503) {
        setRosterError(
          `${guildName} isn't cached yet and Census is currently unavailable. Try again shortly.`
        )
      } else if (!rosterRes.ok) {
        setRosterError((await rosterRes.json().catch(() => ({}))).detail ?? `Error ${rosterRes.status}`)
      } else {
        setRoster(await rosterRes.json())
      }
      if (infoRes.ok) setInfo(await infoRes.json())
      if (officerRes.ok) {
        const d = await officerRes.json()
        setIsOfficer(d.is_officer === true)
      }
    })
      .catch(() => setRosterError('Network error — please try again.'))
      .finally(() => setRosterLoading(false))
  }, [guildName])

  // SSE live-swap: replace roster state when the server pushes a fresh record.
  const rosterName  = roster?.name
  const rosterWorld = roster?.world
  useEffect(() => {
    if (!rosterName || !rosterWorld) return
    const key = `guild:${rosterName.toLowerCase()}:${rosterWorld.toLowerCase()}`
    return subscribe<GuildData>(key, (data) => {
      setRoster(data)
    })
  }, [rosterName, rosterWorld, subscribe])

  // Load spell check when tab first selected
  function loadSpells() {
    if (spells || spellsLoading || !guildName) return
    runSpells(`/api/guild/${encodeURIComponent(guildName)}/spell-check`)
  }

  // Load adorn check when tab first selected
  function loadAdorns() {
    if (adorns || adornsLoading || !guildName) return
    runAdorns(`/api/guild/${encodeURIComponent(guildName)}/adorn-check`)
  }

  function switchTab(t: Tab) {
    setTab(t)
    setFilter('')
    if (t === 'spells') loadSpells()
    if (t === 'adorns') loadAdorns()
  }

  const currentDiscordId = auth.status === 'authenticated' ? auth.user.id : ''

  const guildDisplayName = roster?.name ?? spells?.guild_name ?? adorns?.guild_name ?? '…'
  const guildWorld = roster?.world ?? ''
  const memberCount = roster?.members.length

  // Unique ranks ordered by rank_id, derived from roster
  const ranksOrdered = useMemo(() => {
    if (!roster) return []
    const seen = new Map<string, number>()
    for (const m of roster.members) {
      if (m.rank && !seen.has(m.rank)) seen.set(m.rank, m.rank_id ?? 9999)
    }
    return [...seen.entries()]
      .sort((a, b) => a[1] - b[1])
      .map(([name]) => name)
  }, [roster])

  function toggleRank(rank: string) {
    setHiddenRanks(prev => {
      const next = new Set(prev)
      next.has(rank) ? next.delete(rank) : next.add(rank)
      return next
    })
  }

  const isLoading = tab === 'roster' ? rosterLoading
    : tab === 'spells' ? spellsLoading
    : tab === 'adorns' ? adornsLoading
    : false   // claims / watch tabs handle their own loading state

  const error = tab === 'roster' ? rosterError
    : tab === 'spells' ? spellsError
    : tab === 'adorns' ? adornsError
    : null

  return (
    <main className="max-w-[1000px] mx-auto my-12 px-4">
      <Breadcrumb items={[{ label: 'Guilds', to: '/guilds' }, { label: guildName ?? '…' }]} />

      {/* Header */}
      <div className="mt-4 mb-6">
        <h1
          className="font-heading text-[2.2rem] font-bold tracking-[0.06em] leading-[1.1] mb-1 inline-block"
          style={{
            background: 'linear-gradient(135deg, var(--gold) 0%, var(--gold-bright) 40%, var(--gold) 70%, var(--gold-dim) 100%)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            backgroundClip: 'text',
          }}
        >
          {guildDisplayName}
        </h1>
        {guildWorld && (
          <div className="text-text-muted text-[0.88rem] mb-1">
            {guildWorld}{memberCount != null ? ` · ${memberCount} members` : ''}
          </div>
        )}
        <div className="mb-4">
          <FreshnessBadge stale={roster?.stale} />
        </div>

        {/* Guild info panel */}
        {info && (
          <Card className="flex flex-wrap gap-x-6 gap-y-2 px-[1.1rem] py-3">
            {info.level    != null && <InfoStat label="Guild Level"  value={String(info.level)} />}
            {info.members  != null && <InfoStat label="Characters"   value={String(info.members)} />}
            {info.accounts != null && <InfoStat label="Accounts"     value={String(info.accounts)} />}
            {info.achievement_count > 0 && <InfoStat label="Achievements" value={String(info.achievement_count)} />}
            {info.alignment && <InfoStat label="Alignment" value={info.alignment} />}
            {info.type      && <InfoStat label="Type"      value={info.type} />}
            {info.dateformed && (
              <InfoStat label="Founded" value={fmtLocalDate(info.dateformed)} />
            )}
            {info.description && (
              <div className="w-full pt-1.5 border-t border-border mt-1">
                <span className="text-[0.75rem] text-text-muted uppercase tracking-[0.06em]">Description</span>
                <p className="text-[0.88rem] text-text mt-1 leading-normal">{info.description}</p>
              </div>
            )}
          </Card>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1.5 mb-4 flex-wrap border-b border-border">
        <TabButton active={tab === 'roster'} onClick={() => switchTab('roster')}>Roster</TabButton>
        <TabButton active={tab === 'spells'} onClick={() => switchTab('spells')}>Spell Check</TabButton>
        <TabButton active={tab === 'adorns'} onClick={() => switchTab('adorns')}>Adorn Check</TabButton>
        {isOfficer && (
          <TabButton active={tab === 'claims'} onClick={() => switchTab('claims')}>Claim Requests</TabButton>
        )}
        {isOfficer && (
          <TabButton active={tab === 'watch'} onClick={() => switchTab('watch')}>Item Watch</TabButton>
        )}
      </div>

      {/* Filters — hidden on claims and watch tabs */}
      {tab !== 'claims' && tab !== 'watch' && !isLoading && !error && (
        <div className="mb-3 flex flex-col gap-2">
          <input
            type="text"
            placeholder="Filter by name, class or rank…"
            value={filter}
            onChange={e => setFilter(e.target.value)}
            className="max-w-[300px] box-border"
          />
          {ranksOrdered.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[0.72rem] text-text-muted uppercase tracking-[0.06em] mr-[0.2rem]">
                Ranks
              </span>
              {ranksOrdered.map(rank => (
                <FilterPill key={rank} active={!hiddenRanks.has(rank)} onClick={() => toggleRank(rank)}>
                  {rank}
                </FilterPill>
              ))}
              {hiddenRanks.size > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setHiddenRanks(new Set())}
                >
                  reset
                </Button>
              )}
            </div>
          )}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="text-text-muted mt-2">
          <p>
            {tab === 'spells'
              ? 'Loading spell data for all members… this takes a minute for large guilds.'
              : 'Fetching guild data…'}
          </p>
        </div>
      )}

      {/* Error */}
      {!isLoading && error && (
        <p className="text-danger">{error}</p>
      )}

      {/* Tables */}
      {tab !== 'claims' && tab !== 'watch' && !isLoading && !error && (
        <Card className="p-0 overflow-x-auto">
          {tab === 'roster' && roster && (
            <GuildRosterTab members={roster.members} filter={filter} hiddenRanks={hiddenRanks} myChars={myChars} />
          )}
          {tab === 'spells' && spells && (
            <GuildSpellCheckTab data={spells} filter={filter} hiddenRanks={hiddenRanks} myChars={myChars} />
          )}
          {tab === 'adorns' && adorns && (
            <GuildAdornCheckTab data={adorns} filter={filter} hiddenRanks={hiddenRanks} myChars={myChars} />
          )}
        </Card>
      )}

      {/* Claim requests — officers only, self-contained loading */}
      {tab === 'claims' && isOfficer && guildName && (
        <Card className="p-0">
          <ClaimRequestsTab guildName={guildName} currentDiscordId={currentDiscordId} />
        </Card>
      )}

      {/* Item watch — officers only, self-contained loading */}
      {tab === 'watch' && isOfficer && guildName && (
        <Card className="p-0">
          <ItemWatchTab guildName={guildName} />
        </Card>
      )}
    </main>
  )
}
