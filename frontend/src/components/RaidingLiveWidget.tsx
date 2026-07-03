import { useEffect, useRef, useState } from 'react'

interface LiveEntry {
  guild_name: string
  team_name: string
  twitch_login: string
  twitch_url: string
  viewer_count: number | null
  title: string | null
  started_at: string | null
}

const POLL_MS = 60_000

/**
 * Site-wide "Raiding live" indicator: a red "● N live" pill in the header that
 * expands to a dropdown of guilds currently within a raid window AND live on
 * Twitch (per the raid_live poller). Auto-expands when a raid goes live; the
 * user can minimise it back to the pill. Renders nothing when nobody is live.
 */
export default function RaidingLiveWidget() {
  const [live, setLive] = useState<LiveEntry[]>([])
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  // Whether we've already auto-expanded for the current live session, so a user
  // who minimises the dropdown isn't re-opened on the next poll.
  const autoOpenedRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const res = await fetch('/api/raiding-live', { credentials: 'include' })
        if (res.ok && !cancelled) setLive(await res.json())
      } catch {
        /* transient — keep the last known list */
      }
    }
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  // Auto-expand once when a raid goes live; reset when the list empties so the
  // next live session expands again. The user can still minimise in between.
  useEffect(() => {
    if (live.length > 0 && !autoOpenedRef.current) {
      autoOpenedRef.current = true
      setOpen(true)
    } else if (live.length === 0 && autoOpenedRef.current) {
      autoOpenedRef.current = false
      setOpen(false)
    }
  }, [live])

  useEffect(() => {
    if (!open) return
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  if (live.length === 0) return null

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        title={open ? 'Minimise' : 'Guilds raiding live right now'}
        aria-expanded={open}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-danger/15 border border-danger/40 text-[0.78rem] font-semibold text-danger cursor-pointer appearance-none"
      >
        <span className="w-2 h-2 rounded-full bg-danger animate-pulse" />
        {live.length} live
        <span className={`text-[0.6rem] transition-transform ${open ? 'rotate-180' : ''}`}>▾</span>
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-[280px] rounded-md border border-gold/40 bg-surface-raised shadow-[0_14px_36px_rgba(0,0,0,0.7)] z-dropdown p-1">
          <div className="px-2 py-1 text-[0.62rem] uppercase tracking-[0.12em] text-text-muted">Raiding live now</div>
          {live.map((e, i) => (
            <a
              key={`${e.guild_name}-${e.team_name}-${i}`}
              href={e.twitch_url}
              target="_blank"
              rel="noopener noreferrer"
              className="block px-2 py-1.5 rounded-sm no-underline hover:bg-gold/10"
            >
              <div className="text-[0.85rem] text-text font-medium truncate">
                {e.guild_name}
                <span className="text-text-muted font-normal"> · {e.team_name}</span>
              </div>
              <div className="text-[0.72rem] text-text-muted flex items-center gap-1.5">
                <span style={{ color: '#9146FF' }}>Watch on Twitch ↗</span>
                {e.viewer_count != null && <span>· {e.viewer_count.toLocaleString()} viewers</span>}
              </div>
            </a>
          ))}
        </div>
      )}
    </div>
  )
}
