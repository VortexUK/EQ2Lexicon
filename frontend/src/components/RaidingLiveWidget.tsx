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
 * Twitch (per the raid_live poller). Renders nothing when nobody is live.
 */
export default function RaidingLiveWidget() {
  const [live, setLive] = useState<LiveEntry[]>([])
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

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
        title="Guilds raiding live right now"
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-danger/15 border border-danger/40 text-[0.78rem] font-semibold text-danger cursor-pointer appearance-none"
      >
        <span className="w-2 h-2 rounded-full bg-danger animate-pulse" />
        {live.length} live
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
