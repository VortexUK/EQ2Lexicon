import { useEffect, useState } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { fmtRelative } from '../formatters'
import { useAuth } from '../hooks/useAuth'
import { Button, SectionLabel } from './ui'

// ── Types ─────────────────────────────────────────────────────────────────────

interface StrategyResponse {
  zone_name: string
  encounter_name: string
  position: number
  markdown: string
  last_edited_at: number | null
  last_edited_by: string | null
  source: string
}

interface RevisionEntry {
  id: number
  edited_at: number
  edited_by: string
  before_md: string | null
  after_md: string
  edit_note: string | null
}

interface RevisionListResponse {
  zone_name: string
  encounter_name: string
  position: number
  revisions: RevisionEntry[]
}

interface Props {
  zoneName: string
  position: number
  /** Fallback wiki URL surfaced in the empty-state copy when there's no strategy. */
  wikiUrl: string | null
}

// ── Markdown styling ──────────────────────────────────────────────────────────
//
// Tailwind Preflight isn't imported (see CLAUDE.md / index.css), so without
// these overrides headings/lists would render with browser-default sizes and
// our `* { padding: 0 }` reset would hide bullet markers. Kept inline here so
// the strategy view's typography lives in one place.

const MARKDOWN_COMPONENTS: Components = {
  h1: props => <h1 className="font-heading text-gold-bright text-[1.25rem] mt-4 mb-2" {...props} />,
  h2: props => <h2 className="font-heading text-gold-bright text-[1.1rem] mt-3 mb-2" {...props} />,
  h3: props => <h3 className="font-heading text-gold text-[1rem] mt-3 mb-1" {...props} />,
  p:  props => <p className="mb-2 leading-relaxed" {...props} />,
  ul: props => <ul className="list-disc pl-5 mb-2 space-y-1" {...props} />,
  ol: props => <ol className="list-decimal pl-5 mb-2 space-y-1" {...props} />,
  li: props => <li className="leading-relaxed" {...props} />,
  a:  props => (
    <a
      className="text-gold underline decoration-dotted underline-offset-2 hover:text-gold-bright"
      target="_blank"
      rel="noopener noreferrer"
      {...props}
    />
  ),
  code: ({ className, children, ...rest }) => {
    // Inline code vs fenced block — react-markdown puts `language-*` on the
    // outer <pre><code className="language-…">, nothing on inline <code>.
    const isInline = !className
    return isInline ? (
      <code className="bg-surface-raised/70 border border-border rounded-sm px-1 text-[0.85em]" {...rest}>
        {children}
      </code>
    ) : (
      <code className={className} {...rest}>{children}</code>
    )
  },
  pre: props => (
    <pre
      className="bg-bg/60 border border-border rounded-md p-3 overflow-x-auto text-[0.85em] mb-2"
      {...props}
    />
  ),
  blockquote: props => (
    <blockquote className="border-l-2 border-gold-dim pl-3 text-text-muted italic mb-2" {...props} />
  ),
  hr: () => <hr className="border-border my-3" />,
  table: props => <table className="w-full border-collapse mb-2 text-sm" {...props} />,
  th: props => <th className="text-left border border-border px-2 py-1 bg-surface-raised/50" {...props} />,
  td: props => <td className="border border-border px-2 py-1" {...props} />,
  strong: props => <strong className="text-text font-semibold" {...props} />,
}

// ── Component ─────────────────────────────────────────────────────────────────

export function EncounterStrategy({ zoneName, position, wikiUrl }: Props) {
  const auth = useAuth()
  const isAdmin = auth.status === 'authenticated' && auth.user.is_admin

  const [data, setData] = useState<StrategyResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [preview, setPreview] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // History disclosure state. Lazy-loaded — we don't fetch revisions until the
  // user actually opens the disclosure, since most viewers never will.
  const [revisions, setRevisions] = useState<RevisionEntry[] | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)

  // Fetch when (zone, position) changes. 404 is the expected "no strategy yet"
  // state and resets data to null — the empty-state UI renders from that.
  // Also resets the history disclosure so a stale list doesn't leak across
  // encounter switches.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setData(null)
    setEditing(false)
    setDraft('')
    setRevisions(null)
    setHistoryOpen(false)
    setHistoryError(null)

    fetch(`/api/zones/${encodeURIComponent(zoneName)}/encounters/${position}/strategy`, {
      credentials: 'include',
    })
      .then(async r => {
        if (r.status === 404) return null
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<StrategyResponse>
      })
      .then(j => { if (!cancelled) setData(j) })
      .catch(err => { if (!cancelled) setError(String(err.message ?? err)) })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [zoneName, position])

  async function toggleHistory() {
    // Close-on-second-click. Cached after first fetch unless invalidated (a
    // successful save below clears `revisions` so the next open re-fetches).
    if (historyOpen) {
      setHistoryOpen(false)
      return
    }
    setHistoryOpen(true)
    if (revisions !== null) return  // already loaded

    setHistoryLoading(true)
    setHistoryError(null)
    try {
      const r = await fetch(
        `/api/zones/${encodeURIComponent(zoneName)}/encounters/${position}/strategy/revisions`,
        { credentials: 'include' }
      )
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
      const j = (await r.json()) as RevisionListResponse
      setRevisions(j.revisions)
    } catch (err) {
      setHistoryError(String((err as Error).message ?? err))
    } finally {
      setHistoryLoading(false)
    }
  }

  function startEdit() {
    setDraft(data?.markdown ?? '')
    setPreview(false)
    setEditing(true)
    setError(null)
  }

  function cancelEdit() {
    setEditing(false)
    setDraft('')
    setError(null)
  }

  async function save() {
    if (!draft.trim()) {
      setError('Strategy body is empty.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const r = await fetch(
        `/api/zones/${encodeURIComponent(zoneName)}/encounters/${position}/strategy`,
        {
          method: 'PUT',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ markdown: draft }),
        }
      )
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
      const fresh = (await r.json()) as StrategyResponse
      setData(fresh)
      setEditing(false)
      setDraft('')
      // The save just appended a new revision row server-side; drop the cache
      // so the next history-open fetches fresh.
      setRevisions(null)
    } catch (err) {
      setError(String((err as Error).message ?? err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <header className="flex items-baseline justify-between flex-wrap gap-2 mb-1">
        <SectionLabel>Strategy</SectionLabel>
        {isAdmin && !editing && (
          <Button size="sm" variant="secondary" onClick={startEdit}>
            {data ? 'Edit' : 'Write strategy'}
          </Button>
        )}
      </header>

      {loading && <p className="text-text-muted text-sm">Loading…</p>}

      {!loading && !editing && data && (
        <>
          <div className="text-text text-[0.95rem]">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
              {data.markdown}
            </ReactMarkdown>
          </div>
          <div className="flex items-baseline justify-between flex-wrap gap-2 mt-2 text-[0.72rem]">
            {data.last_edited_at ? (
              <p className="text-text-muted">
                Edited {fmtRelative(data.last_edited_at)}
                {data.last_edited_by ? ` · ${data.last_edited_by}` : ''}
              </p>
            ) : <span />}
            <button
              type="button"
              onClick={toggleHistory}
              className="text-gold-dim hover:text-gold underline decoration-dotted underline-offset-2"
              aria-expanded={historyOpen}
            >
              {historyOpen ? 'Hide history' : 'Show history'}
            </button>
          </div>
          {historyOpen && (
            <RevisionsPanel
              revisions={revisions}
              loading={historyLoading}
              error={historyError}
            />
          )}
        </>
      )}

      {!loading && !editing && !data && (
        <p className="text-text-muted text-sm leading-relaxed">
          No strategy written yet for this encounter.{' '}
          {isAdmin ? (
            <>Click <em>Write strategy</em> above to add one.</>
          ) : wikiUrl ? (
            <>
              The{' '}
              <a
                href={wikiUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-gold-dim underline decoration-dotted underline-offset-2 hover:text-gold"
              >
                EQ2i wiki page
              </a>{' '}
              is the best reference for now.
            </>
          ) : (
            <>The EQ2i wiki is the best reference for now.</>
          )}
        </p>
      )}

      {editing && (
        <Editor
          draft={draft}
          onDraft={setDraft}
          preview={preview}
          onPreview={setPreview}
          saving={saving}
          error={error}
          onSave={save}
          onCancel={cancelEdit}
        />
      )}

      {!editing && error && (
        <p className="text-danger text-sm mt-2">Couldn't load strategy: {error}</p>
      )}
    </section>
  )
}

// ── Editor subcomponent ───────────────────────────────────────────────────────

interface EditorProps {
  draft: string
  onDraft: (s: string) => void
  preview: boolean
  onPreview: (b: boolean) => void
  saving: boolean
  error: string | null
  onSave: () => void
  onCancel: () => void
}

function Editor({ draft, onDraft, preview, onPreview, saving, error, onSave, onCancel }: EditorProps) {
  return (
    <div className="mt-2 flex flex-col gap-2">
      <div className="flex items-center gap-1 text-[0.78rem]">
        <button
          type="button"
          onClick={() => onPreview(false)}
          className={
            'px-2 py-1 rounded-sm border ' +
            (!preview ? 'bg-surface-raised border-gold/40 text-gold-bright' : 'border-border text-text-muted hover:text-text')
          }
        >
          Write
        </button>
        <button
          type="button"
          onClick={() => onPreview(true)}
          className={
            'px-2 py-1 rounded-sm border ' +
            (preview ? 'bg-surface-raised border-gold/40 text-gold-bright' : 'border-border text-text-muted hover:text-text')
          }
        >
          Preview
        </button>
        <span className="ml-auto text-text-muted text-[0.72rem]">Markdown · GFM tables supported</span>
      </div>

      {!preview ? (
        <textarea
          value={draft}
          onChange={e => onDraft(e.target.value)}
          rows={16}
          spellCheck={false}
          className="w-full bg-bg/60 border border-border rounded-md p-3 font-mono text-[0.88rem] leading-relaxed text-text outline-none focus:border-gold/60 resize-y"
          placeholder={'## Tactics\n\n- Tank positioning\n- Healer assignments\n- Cure rotations\n'}
        />
      ) : (
        <div className="border border-border rounded-md p-3 min-h-[20rem] text-text text-[0.95rem]">
          {draft.trim() ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
              {draft}
            </ReactMarkdown>
          ) : (
            <p className="text-text-muted italic">Nothing to preview yet.</p>
          )}
        </div>
      )}

      {error && <p className="text-danger text-sm">{error}</p>}

      <div className="flex items-center gap-2 justify-end">
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={saving}>Cancel</Button>
        <Button size="sm" variant="primary" onClick={onSave} disabled={saving || !draft.trim()}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  )
}

// ── Revisions panel ───────────────────────────────────────────────────────────

interface RevisionsPanelProps {
  revisions: RevisionEntry[] | null
  loading: boolean
  error: string | null
}

function RevisionsPanel({ revisions, loading, error }: RevisionsPanelProps) {
  if (loading) return <p className="text-text-muted text-sm mt-2">Loading history…</p>
  if (error) return <p className="text-danger text-sm mt-2">Couldn't load history: {error}</p>
  if (revisions === null) return null
  if (revisions.length === 0) {
    return <p className="text-text-muted text-sm mt-2">No history yet for this encounter.</p>
  }
  return (
    <ol className="mt-3 border border-border rounded-md divide-y divide-border/60 overflow-hidden">
      {revisions.map((rev, i) => (
        <RevisionRow key={rev.id} revision={rev} isCurrent={i === 0} />
      ))}
    </ol>
  )
}

interface RevisionRowProps {
  revision: RevisionEntry
  /** True for index 0 — newest revision == what's currently rendered above. */
  isCurrent: boolean
}

function RevisionRow({ revision, isCurrent }: RevisionRowProps) {
  // Per-row open state — multiple revisions can be expanded at once so a user
  // can compare two manually without losing context.
  const [open, setOpen] = useState(false)
  const absoluteTime = new Date(revision.edited_at * 1000).toLocaleString()
  return (
    <li className="bg-surface-raised/30">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-baseline gap-3 px-3 py-2 text-left hover:bg-surface-raised/60"
        aria-expanded={open}
      >
        <span className="text-[0.72rem] text-gold-dim w-4 shrink-0">
          {open ? '▾' : '▸'}
        </span>
        <span className="flex-1 min-w-0 text-[0.85rem] text-text" title={absoluteTime}>
          {fmtRelative(revision.edited_at)}
          {isCurrent && <span className="ml-2 text-success text-[0.7rem] uppercase tracking-[0.08em]">current</span>}
          {revision.edit_note && (
            <span className="text-text-muted"> · {revision.edit_note}</span>
          )}
        </span>
        <span className="text-text-muted text-[0.72rem] shrink-0">{revision.edited_by}</span>
      </button>
      {open && (
        <div className="px-3 pb-3 pt-1 border-t border-border/60 bg-bg/40 text-text text-[0.92rem]">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
            {revision.after_md}
          </ReactMarkdown>
        </div>
      )}
    </li>
  )
}
