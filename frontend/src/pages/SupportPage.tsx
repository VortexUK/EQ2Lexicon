import { useEffect, useState } from 'react'
import { SupporterBadge } from '../components/SupporterBadge'
import { LinkButton } from '../components/ui'

/**
 * /support — the donations + "what hosting costs" page.
 *
 * Linked from the footer. Pitches GitHub Sponsors as the primary route;
 * lists the current supporter wall (rendered from /api/supporters) at
 * the bottom for the social-proof + thank-you effect.
 *
 * Update the SPONSOR_URL constant below once the GitHub Sponsors page
 * is live. The site keeps shipping fine in the meantime — clicking the
 * button just lands on the (not-yet-live) sponsors page on GitHub.
 */

// TODO: replace with the real GitHub Sponsors URL once the account is
// set up. Leaving the VortexUK org slug as a sensible placeholder so the
// link points to the correct future location.
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
        // Pull supporter IDs and (best-effort) their display names so the
        // wall shows recognisable names rather than opaque snowflakes.
        // Falls back to "anonymous" formatting if the lookup endpoint
        // doesn't expose names.
        const r = await fetch('/api/supporters')
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const body = (await r.json()) as { supporter_ids: string[] }
        if (cancelled) return
        setRows(body.supporter_ids.map((id) => ({ discord_id: id, display_name: null })))
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
            <strong className="text-text">Backups.</strong> Litestream
            replicates the parses, raids, and users databases to R2 so
            nothing is ever a single VM crash away from being gone.
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
          Names are anonymised by default — if you'd like to be listed by
          name reach out on Discord.
        </p>
      </section>
    </div>
  )
}
