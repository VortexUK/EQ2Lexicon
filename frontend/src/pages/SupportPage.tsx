import { useEffect, useState } from 'react'
import { DiscordCommunityLink } from '../components/DiscordCommunityLink'
import { SupporterBadge } from '../components/SupporterBadge'
import { LinkButton } from '../components/ui'

/**
 * /support — the donations + "what hosting costs" page: GitHub Sponsors
 * link plus the supporter wall from /api/supporters.
 */

const SPONSOR_URL = 'https://github.com/sponsors/VortexUK'

interface SupporterRow {
  discord_id: string
  display_name: string | null
}

export default function SupportPage() {
  const [rows, setRows] = useState<SupporterRow[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        // The endpoint returns each supporter with the Discord display
        // name we hold, so the wall shows recognisable names; the id-only
        // fallback below is for a row whose users record carries no name.
        const r = await fetch('/api/supporters', { credentials: 'include' })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const body = (await r.json()) as { supporters?: SupporterRow[]; supporter_ids: string[] }
        if (cancelled) return
        setRows(body.supporters ?? body.supporter_ids.map((id) => ({ discord_id: id, display_name: null })))
      } catch {
        if (!cancelled) setRows([])
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="mx-auto max-w-2xl px-4 py-10 space-y-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold text-text">Support the site</h1>
        <p className="text-text-muted">
          EQ2 Lexicon is a free, community-run tool. If you find it useful and
          you'd like to throw a coin its way, the link below covers hosting,
          backups, and the occasional caffeinated late-night feature push.
        </p>
      </header>

      <section className="space-y-3 rounded-lg border border-border bg-bg-card p-5">
        <h2 className="text-lg font-medium text-text">Where the money goes</h2>
        <ul className="list-disc pl-5 text-sm text-text-muted space-y-1">
          <li>
            <strong className="text-text">Hosting.</strong> Railway runs the
            FastAPI app + frontend; usage scales with parse uploads + Census
            traffic.
          </li>
          <li>
            <strong className="text-text">Backups.</strong> The database
            (parses, guilds, strategies, accounts) is dumped nightly to
            off-site storage so nothing is ever a single outage away from
            being gone.
          </li>
          <li>
            <strong className="text-text">Domain + monitoring.</strong>{' '}
            <code>eq2lexicon.com</code> plus the metrics stack that keeps
            an eye on it.
          </li>
          <li>
            <strong className="text-text">Time.</strong> Honestly the largest
            cost. Donations are a way of saying "please keep going."
          </li>
        </ul>
      </section>

      <section className="space-y-4 rounded-lg border border-border bg-bg-card p-5">
        <h2 className="text-lg font-medium text-text">Become a supporter</h2>
        <p className="text-sm text-text-muted">
          Supporters get a <SupporterBadge /> next to their name across the
          site — visible on raid strategy edits, contributions, and any
          other place names appear. No extra features are gated behind
          donations: everything stays free for everyone.
        </p>
        <LinkButton
          href={SPONSOR_URL}
          target="_blank"
          rel="noopener noreferrer"
          variant="primary"
        >
          Sponsor on GitHub →
        </LinkButton>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-medium text-text">Current supporters</h2>
        {loading ? (
          <p className="text-sm text-text-muted">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-text-muted">
            No supporters yet — be the first <SupporterBadge />
          </p>
        ) : (
          <ul className="space-y-1 text-sm text-text">
            {rows.map((r) => (
              <li key={r.discord_id} className="flex items-center">
                <span>{r.display_name || `Supporter #${r.discord_id.slice(-4)}`}</span>
                <SupporterBadge />
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-text-muted pt-2">
          Supporters are listed by their Discord display name. If you&apos;d
          rather not appear here, reach out on Discord and the badge stays
          while the listing goes.
        </p>
        <div className="pt-2">
          <DiscordCommunityLink variant="button" />
        </div>
      </section>
    </div>
  )
}
