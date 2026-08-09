import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Card, SectionLabel, Badge, LinkButton } from '../components/ui'

/**
 * /downloads — the single hub for the two ways to feed parses into the site:
 * the standalone EQ2Parser desktop app (recommended) and the legacy ACT
 * plugin (for people who already run Advanced Combat Tracker).
 *
 * Both download links use GitHub's `releases/latest/download/<asset>`
 * redirect, so they always resolve to the newest release without this page
 * hardcoding a version. Asset names are stable across releases:
 *   - EQ2Parser-win-Setup.exe / -Portable.zip  (Velopack output)
 *   - EQ2Lexicon.ACTPlugin.dll
 */

const PARSER_SETUP    = 'https://github.com/VortexUK/EQ2Parser/releases/latest/download/EQ2Parser-win-Setup.exe'
const PARSER_PORTABLE = 'https://github.com/VortexUK/EQ2Parser/releases/latest/download/EQ2Parser-win-Portable.zip'
const PARSER_RELEASES = 'https://github.com/VortexUK/EQ2Parser/releases/latest'
const PLUGIN_DLL      = 'https://github.com/VortexUK/EQ2LexiconACTPlugin/releases/latest/download/EQ2Lexicon.ACTPlugin.dll'
const PLUGIN_RELEASES = 'https://github.com/VortexUK/EQ2LexiconACTPlugin/releases/latest'

function Code({ children }: { children: ReactNode }) {
  return (
    <code className="font-mono text-[0.8rem] text-gold-dim bg-surface px-1.5 py-0.5 rounded-sm break-words">
      {children}
    </code>
  )
}

function Step({ children }: { children: ReactNode }) {
  return <li className="pl-1 leading-relaxed">{children}</li>
}

/**
 * A compact screenshot thumbnail that expands to a full-screen lightbox on
 * click. Kept small inline so it previews the app without dominating the
 * card / stealing focus from the download CTA. Dismiss the lightbox with the
 * ✕ button, the Escape key, or a click anywhere on the backdrop/image.
 */
function Screenshot({ src, alt, caption }: { src: string; alt: string; caption: string }) {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [open])

  return (
    <figure className="space-y-1.5">
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Enlarge screenshot"
        className="group relative block w-full max-w-[380px] appearance-none border-0 bg-transparent p-0 cursor-zoom-in"
      >
        <img
          src={src}
          alt={alt}
          width={1600}
          height={1176}
          loading="lazy"
          className="w-full h-auto rounded-md border border-border shadow-md transition-[filter] duration-150 group-hover:brightness-[1.06]"
        />
        <span className="absolute bottom-1.5 right-1.5 rounded-sm bg-bg/80 border border-border px-1.5 py-0.5 text-[0.65rem] text-text-muted opacity-0 transition-opacity group-hover:opacity-100">
          ⤢ Enlarge
        </span>
      </button>
      <figcaption className="text-xs text-text-muted max-w-[380px]">{caption}</figcaption>

      {open && (
        <div
          className="fixed inset-0 z-modal flex items-center justify-center p-4 sm:p-8 bg-bg/85 backdrop-blur-sm cursor-zoom-out"
          onClick={() => setOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label="Screenshot enlarged"
        >
          <img src={src} alt={alt} className="max-w-full max-h-full w-auto h-auto rounded-md border border-border shadow-2xl" />
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close"
            className="absolute top-3 right-3 flex h-9 w-9 items-center justify-center rounded-md border border-border bg-bg/80 text-lg text-text-muted transition-colors hover:text-gold cursor-pointer appearance-none"
          >
            ✕
          </button>
        </div>
      )}
    </figure>
  )
}

export default function DownloadsPage() {
  return (
    <div className="mx-auto max-w-3xl px-4 py-10 space-y-8">
      <header className="space-y-2">
        <h1 className="font-heading text-3xl text-gold">Downloads</h1>
        <p className="text-text-muted leading-relaxed">
          Both tools below parse your EverQuest&nbsp;II combat log and upload finished
          fights to EQ2&nbsp;Lexicon, where they show up under your guild on{' '}
          <Link to="/parses" className="text-gold-dim hover:text-gold underline underline-offset-2">Parses</Link>{' '}
          and feed the{' '}
          <Link to="/rankings" className="text-gold-dim hover:text-gold underline underline-offset-2">Rankings</Link>.
          New here? Get <strong className="text-text">EQ2Parser</strong> — it's a standalone app that
          does everything on its own. Already run ACT? The plugin slots into your existing setup.
        </p>
      </header>

      {/* ── EQ2Parser (recommended) ─────────────────────────────── */}
      <Card className="space-y-5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h2 className="font-heading text-2xl text-text">EQ2Parser</h2>
          <Badge variant="gold">Recommended</Badge>
          <Badge variant="muted">Windows</Badge>
        </div>

        <p className="text-sm text-text-muted leading-relaxed">
          A modern, native Windows combat parser built for EQ2&nbsp;TLE raiding — no
          Advanced Combat Tracker required. Live DPS/HPS breakdowns, triggers, spell
          timers, a click-through overlay, local encounter history, and one-click parse
          uploads to this site. Open source (MIT) and self-updating.
        </p>

        <Screenshot
          src="/eq2parser-screenshot.webp"
          alt="EQ2Parser's Main tab showing a live raid parse — a per-combatant DPS bar chart above a sortable damage table, with the fight history tree on the left."
          caption="The Main tab mid-raid — DPS chart, sortable damage table, and fight history. Click to enlarge."
        />

        <div className="flex flex-wrap items-center gap-3">
          <LinkButton href={PARSER_SETUP} variant="primary">
            Download for Windows
          </LinkButton>
          <LinkButton href={PARSER_PORTABLE} variant="secondary" size="sm">
            Portable (.zip)
          </LinkButton>
          <a
            href={PARSER_RELEASES}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[0.8rem] text-text-muted hover:text-gold underline underline-offset-2"
          >
            All releases &amp; source →
          </a>
        </div>

        <div className="space-y-2">
          <SectionLabel>Setup</SectionLabel>
          <ol className="list-decimal pl-5 space-y-1.5 text-sm text-text-muted marker:text-gold-dim">
            <Step>
              Download and run <strong className="text-text">EQ2Parser-win-Setup.exe</strong>. It
              installs per-user (no admin needed) and updates itself automatically from then on.
            </Step>
            <Step>
              In EverQuest&nbsp;II, turn on combat logging: type <Code>/log on</Code>. The parser
              reads your <Code>eq2log_&lt;character&gt;.txt</Code> file.
            </Step>
            <Step>
              On this site, open <Link to="/settings/tokens" className="text-gold-dim hover:text-gold underline underline-offset-2">API&nbsp;tokens</Link>{' '}
              and create a token.
            </Step>
            <Step>
              In EQ2Parser: <strong className="text-text">Settings → Parse uploads</strong>, tick
              "Upload finished fights", paste your token, hit <strong className="text-text">Save</strong>,
              then <strong className="text-text">Test</strong>.
            </Step>
            <Step>
              Done — finished fights upload in the background. You can also right-click any fight in the
              tree and choose <strong className="text-text">Upload to EQ2Lexicon</strong>.
            </Step>
          </ol>
          <p className="text-xs text-text-muted pt-1">
            Prefer not to install? The Portable (.zip) runs from any folder — same app, no installer.
          </p>
        </div>
      </Card>

      {/* ── ACT plugin (for existing ACT users) ─────────────────── */}
      <Card className="space-y-5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h2 className="font-heading text-2xl text-text">ACT Plugin</h2>
          <Badge variant="muted">For ACT users</Badge>
        </div>

        <p className="text-sm text-text-muted leading-relaxed">
          A plugin for{' '}
          <a href="https://advancedcombattracker.com" target="_blank" rel="noopener noreferrer"
            className="text-gold-dim hover:text-gold underline underline-offset-2">Advanced Combat Tracker</a>{' '}
          that uploads your EQ2 encounters here automatically as each fight ends. Use this if you
          already run ACT with EQ2 parsing set up — otherwise the standalone EQ2Parser above is the
          simpler path.
        </p>

        <div className="flex flex-wrap items-center gap-3">
          <LinkButton href={PLUGIN_DLL} variant="primary">
            Download plugin (.dll)
          </LinkButton>
          <a
            href={PLUGIN_RELEASES}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[0.8rem] text-text-muted hover:text-gold underline underline-offset-2"
          >
            All releases &amp; source →
          </a>
        </div>

        <div className="space-y-2">
          <SectionLabel>Setup</SectionLabel>
          <ol className="list-decimal pl-5 space-y-1.5 text-sm text-text-muted marker:text-gold-dim">
            <Step>
              On this site, create a token at{' '}
              <Link to="/settings/tokens" className="text-gold-dim hover:text-gold underline underline-offset-2">API&nbsp;tokens</Link>.
            </Step>
            <Step>
              Download <strong className="text-text">EQ2Lexicon.ACTPlugin.dll</strong>.
            </Step>
            <Step>
              Drop it into <Code>%APPDATA%\Advanced Combat Tracker\Plugins\</Code>.
            </Step>
            <Step>
              In ACT: <strong className="text-text">Options → Plugins → Browse</strong>, pick the DLL,
              then <strong className="text-text">Add/Enable Plugin</strong>.
            </Step>
            <Step>
              Open the new <strong className="text-text">EQ2 Lexicon</strong> tab in ACT, paste your
              token, hit <strong className="text-text">Test Connection</strong>, then{' '}
              <strong className="text-text">Save</strong>.
            </Step>
          </ol>
          <p className="text-xs text-text-muted pt-1">
            Requires Advanced Combat Tracker with EQ2 parsing already configured.
          </p>
        </div>
      </Card>

      <p className="text-xs text-text-muted text-center">
        Both tools are open source under the MIT licence. An{' '}
        <Link to="/settings/tokens" className="text-gold-dim hover:text-gold underline underline-offset-2">API token</Link>{' '}
        from your account links uploads to you — it's stored encrypted on your own machine.
      </p>
    </div>
  )
}
