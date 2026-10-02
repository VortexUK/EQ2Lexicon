/**
 * GuildRecruitmentTab — the guild's recruitment profile.
 *
 * View mode renders for everyone (the server GET is public); edit controls
 * render for officers/admins and the server enforces the same gate on every
 * write. The logo is uploaded as base64 JSON (2 MB client pre-check), the
 * server re-encodes it to a ≤200px WebP and audits WHO uploaded it; admins
 * also get a remove-logo backstop in view mode.
 */
import { useRef, useState } from 'react'

import { Badge, Button, LinkButton, SectionLabel } from '../../components/ui'
import { Textarea } from '../../components/ui/Textarea'
import { FilterPill } from '../../components/FilterPill'
import { fmtRelative } from '../../formatters'
import { useFetch } from '../../hooks/useFetch'
import { handle } from '../../lib/api'
import { toErrorMessage } from '../../lib/errors'
import { useClasses } from '../../useClasses'
import type { GuildMember, RecruitmentProfile } from './types'

const MAX_DESCRIPTION = 1000
const MAX_CONTACTS = 3
const MAX_LOGO_BYTES = 2 * 1024 * 1024

/** Human label for a tag slug ("eu-friendly" → "EU-friendly"). */
export function tagLabel(slug: string): string {
  if (slug === 'eu-friendly') return 'EU-friendly'
  return slug.charAt(0).toUpperCase() + slug.slice(1).replace(/-/g, ' ')
}

interface Draft {
  recruiting: boolean
  description: string
  classes: string[]
  tags: string[]
  contacts: string[]
  discord_url: string
}

export function GuildRecruitmentTab({
  guildName,
  canEdit,
  isAdminUser,
  members,
}: {
  guildName: string
  canEdit: boolean
  isAdminUser: boolean
  members: GuildMember[] | null
}) {
  const url = `/api/guild/${encodeURIComponent(guildName)}/recruitment`
  const { data, loading, error, refetch } = useFetch<RecruitmentProfile>(url)
  const { classes: classCatalogue, colourFor } = useClasses()

  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [contactInput, setContactInput] = useState('')
  const [logoBusy, setLogoBusy] = useState(false)
  const [logoError, setLogoError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  function startEdit() {
    if (!data) return
    setDraft({
      recruiting: data.recruiting,
      description: data.description,
      classes: [...data.classes],
      tags: [...data.tags],
      contacts: [...data.contacts],
      discord_url: data.discord_url ?? '',
    })
    setSaveError(null)
    setLogoError(null)
    setContactInput('')
    setEditing(true)
  }

  function cancelEdit() {
    setEditing(false)
    setDraft(null)
    setSaveError(null)
    setLogoError(null)
  }

  async function save() {
    if (!draft) return
    setSaving(true)
    setSaveError(null)
    try {
      await handle<RecruitmentProfile>(
        await fetch(url, {
          method: 'PUT',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            recruiting: draft.recruiting,
            description: draft.description,
            classes: draft.classes,
            tags: draft.tags,
            contacts: draft.contacts,
            discord_url: draft.discord_url.trim() || null,
          }),
        }),
      )
      setEditing(false)
      setDraft(null)
      refetch()
    } catch (err) {
      setSaveError(toErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  function toggleIn(list: string[], value: string): string[] {
    return list.includes(value) ? list.filter(v => v !== value) : [...list, value]
  }

  function addContact(raw: string) {
    if (!draft) return
    const name = raw.trim()
    if (!name || draft.contacts.length >= MAX_CONTACTS) return
    const canonical = name.charAt(0).toUpperCase() + name.slice(1).toLowerCase()
    if (draft.contacts.includes(canonical)) return
    setDraft({ ...draft, contacts: [...draft.contacts, canonical] })
    setContactInput('')
  }

  async function uploadLogo(file: File) {
    setLogoError(null)
    if (file.size > MAX_LOGO_BYTES) {
      setLogoError('Logo file is too large — 2 MB max.')
      return
    }
    setLogoBusy(true)
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result))
        reader.onerror = () => reject(new Error('Could not read the file.'))
        reader.readAsDataURL(file)
      })
      const base64 = dataUrl.slice(dataUrl.indexOf(',') + 1)
      await handle(
        await fetch(`${url}/logo`, {
          method: 'PUT',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image_base64: base64 }),
        }),
      )
      refetch()
    } catch (err) {
      setLogoError(toErrorMessage(err))
    } finally {
      setLogoBusy(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  async function removeLogo() {
    setLogoError(null)
    setLogoBusy(true)
    try {
      await handle(await fetch(`${url}/logo`, { method: 'DELETE', credentials: 'include' }))
      refetch()
    } catch (err) {
      setLogoError(toErrorMessage(err))
    } finally {
      setLogoBusy(false)
    }
  }

  if (loading && !data) return <p className="text-text-muted p-4">Loading recruitment profile…</p>
  if (error) return <p className="text-danger p-4">{error}</p>
  if (!data) return null

  const logoUrl = data.has_logo ? `${url}/logo?v=${data.logo_uploaded_at ?? 0}` : null

  // ── Edit mode ───────────────────────────────────────────────────────────
  if (editing && draft) {
    const memberNames = (members ?? [])
      .map(m => m.name)
      .filter(n => !draft.contacts.includes(n))
      .sort()
    return (
      <div className="px-4 py-3 flex flex-col gap-5">
        <SectionLabel>Edit Recruitment Profile</SectionLabel>

        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            className="mt-1"
            checked={draft.recruiting}
            onChange={e => setDraft({ ...draft, recruiting: e.target.checked })}
          />
          <span className="flex flex-col gap-1">
            <span className="text-text">We are recruiting</span>
            <span className="text-[0.8rem] text-text-muted">
              When on, {guildName} appears on the Guilds Recruiting browse page.
            </span>
          </span>
        </label>

        {/* Description */}
        <div className="flex flex-col gap-1.5">
          <span className="text-[0.72rem] text-text-muted uppercase tracking-[0.06em]">Message to applicants</span>
          <Textarea
            value={draft.description}
            maxLength={MAX_DESCRIPTION}
            rows={5}
            placeholder="Who you are, what you raid, what you're looking for…"
            onChange={e => setDraft({ ...draft, description: e.target.value })}
          />
          <span className="text-[0.72rem] text-text-muted self-end">
            {draft.description.length}/{MAX_DESCRIPTION}
          </span>
        </div>

        {/* Needed classes */}
        <div className="flex flex-col gap-1.5">
          <span className="text-[0.72rem] text-text-muted uppercase tracking-[0.06em]">Classes needed</span>
          <div className="flex items-center gap-1.5 flex-wrap">
            {classCatalogue.map(c => (
              <FilterPill
                key={c.name}
                active={draft.classes.includes(c.name)}
                onClick={() => setDraft({ ...draft, classes: toggleIn(draft.classes, c.name) })}
              >
                {c.name}
              </FilterPill>
            ))}
          </div>
        </div>

        {/* Tags */}
        <div className="flex flex-col gap-1.5">
          <span className="text-[0.72rem] text-text-muted uppercase tracking-[0.06em]">Tags</span>
          <div className="flex items-center gap-1.5 flex-wrap">
            {data.available_tags.map(t => (
              <FilterPill
                key={t}
                active={draft.tags.includes(t)}
                onClick={() => setDraft({ ...draft, tags: toggleIn(draft.tags, t) })}
              >
                {tagLabel(t)}
              </FilterPill>
            ))}
          </div>
        </div>

        {/* In-game contacts */}
        <div className="flex flex-col gap-1.5">
          <span className="text-[0.72rem] text-text-muted uppercase tracking-[0.06em]">
            In-game contacts (up to {MAX_CONTACTS}, must be guild members)
          </span>
          <div className="flex items-center gap-1.5 flex-wrap">
            {draft.contacts.map(name => (
              <span
                key={name}
                className="inline-flex items-center gap-1.5 rounded-pill border border-gold/50 bg-gold/15 px-3 py-1 text-[0.82rem] text-gold-bright"
              >
                {name}
                <button
                  type="button"
                  className="appearance-none border-0 bg-transparent cursor-pointer text-gold-bright p-0 leading-none"
                  title={`Remove ${name}`}
                  onClick={() => setDraft({ ...draft, contacts: draft.contacts.filter(c => c !== name) })}
                >
                  ✕
                </button>
              </span>
            ))}
            {draft.contacts.length < MAX_CONTACTS && (
              <>
                <input
                  type="text"
                  list="recruitment-contact-members"
                  placeholder="Character name…"
                  value={contactInput}
                  onChange={e => setContactInput(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      addContact(contactInput)
                    }
                  }}
                  className="w-[180px] text-[0.85rem]"
                />
                <datalist id="recruitment-contact-members">
                  {memberNames.map(n => (
                    <option key={n} value={n} />
                  ))}
                </datalist>
                <Button variant="ghost" size="sm" onClick={() => addContact(contactInput)} disabled={!contactInput.trim()}>
                  Add
                </Button>
              </>
            )}
          </div>
        </div>

        {/* Discord */}
        <div className="flex flex-col gap-1.5">
          <span className="text-[0.72rem] text-text-muted uppercase tracking-[0.06em]">Discord invite</span>
          <input
            type="text"
            placeholder="https://discord.gg/…"
            value={draft.discord_url}
            onChange={e => setDraft({ ...draft, discord_url: e.target.value })}
            className="max-w-[360px] text-[0.88rem]"
          />
          <span className="text-[0.72rem] text-text-muted">
            A permanent discord.gg or discord.com/invite link.
          </span>
        </div>

        {/* Logo */}
        <div className="flex flex-col gap-1.5">
          <span className="text-[0.72rem] text-text-muted uppercase tracking-[0.06em]">Guild logo</span>
          <div className="flex items-center gap-3 flex-wrap">
            {logoUrl && (
              <img src={logoUrl} alt={`${guildName} logo`} className="w-[72px] h-[72px] rounded-md border border-border object-contain bg-surface" />
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp,image/bmp"
              className="hidden"
              onChange={e => {
                const f = e.target.files?.[0]
                if (f) uploadLogo(f)
              }}
            />
            <Button variant="secondary" size="sm" onClick={() => fileInputRef.current?.click()} disabled={logoBusy}>
              {logoBusy ? 'Working…' : data.has_logo ? 'Replace logo' : 'Upload logo'}
            </Button>
            {data.has_logo && (
              <Button variant="danger" size="sm" onClick={removeLogo} disabled={logoBusy}>
                Remove logo
              </Button>
            )}
          </div>
          <span className="text-[0.72rem] text-text-muted">
            PNG, JPEG, GIF, WebP or BMP up to 2 MB — resized to 200×200. Uploads are logged.
          </span>
          {logoError && <span className="text-[0.8rem] text-danger">{logoError}</span>}
        </div>

        {/* Save / cancel */}
        <div className="flex items-center gap-3 flex-wrap pt-1 border-t border-border">
          <Button variant="primary" size="sm" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : 'Save profile'}
          </Button>
          <Button variant="ghost" size="sm" onClick={cancelEdit} disabled={saving}>
            Cancel
          </Button>
          {saveError && <span className="text-[0.8rem] text-danger">{saveError}</span>}
        </div>
      </div>
    )
  }

  // ── View mode ───────────────────────────────────────────────────────────
  const empty =
    !data.recruiting && !data.description && data.classes.length === 0 && data.tags.length === 0 &&
    data.contacts.length === 0 && !data.discord_url && !data.has_logo

  return (
    <div className="px-4 py-3 flex flex-col gap-4">
      <div className="flex items-start gap-4 flex-wrap">
        {logoUrl && (
          <img src={logoUrl} alt={`${guildName} logo`} className="w-[96px] h-[96px] rounded-md border border-border object-contain bg-surface shrink-0" />
        )}
        <div className="flex-1 min-w-[220px] flex flex-col gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            <SectionLabel>Recruitment</SectionLabel>
            <Badge variant={data.recruiting ? 'success' : 'muted'}>
              {data.recruiting ? 'Recruiting' : 'Not recruiting'}
            </Badge>
          </div>
          {data.tags.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap">
              {data.tags.map(t => (
                <Badge key={t} variant="gold">{tagLabel(t)}</Badge>
              ))}
            </div>
          )}
        </div>
        {canEdit && (
          <Button variant="secondary" size="sm" onClick={startEdit}>
            Edit profile
          </Button>
        )}
      </div>

      {empty && (
        <p className="text-text-muted text-[0.88rem] m-0">
          {guildName} hasn&apos;t set up a recruitment profile yet.
          {canEdit ? ' Use "Edit profile" to create one.' : ''}
        </p>
      )}

      {data.description && (
        <p className="text-[0.92rem] text-text leading-relaxed whitespace-pre-line m-0">{data.description}</p>
      )}

      {data.classes.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <span className="text-[0.72rem] text-text-muted uppercase tracking-[0.06em]">Classes needed</span>
          <div className="flex items-center gap-1.5 flex-wrap">
            {data.classes.map(c => (
              <span
                key={c}
                className="rounded-pill border border-border bg-surface px-2.5 py-0.5 text-[0.8rem] font-medium"
                style={{ color: colourFor(c) }}
              >
                {c}
              </span>
            ))}
          </div>
        </div>
      )}

      {data.contacts.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <span className="text-[0.72rem] text-text-muted uppercase tracking-[0.06em]">In-game contacts</span>
          <div className="flex items-center gap-2 flex-wrap">
            {data.contacts.map(name => (
              <a key={name} href={`/character/${encodeURIComponent(name)}`} className="text-gold text-[0.88rem]">
                {name}
              </a>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center gap-3 flex-wrap">
        {data.discord_url && (
          <LinkButton href={data.discord_url} target="_blank" rel="noopener noreferrer" size="sm">
            Join our Discord
          </LinkButton>
        )}
        {/* Admin abuse backstop: remove an offensive logo without entering edit mode */}
        {isAdminUser && data.has_logo && (
          <Button variant="danger" size="sm" onClick={removeLogo} disabled={logoBusy}>
            Remove logo (admin)
          </Button>
        )}
        {logoError && !editing && <span className="text-[0.8rem] text-danger">{logoError}</span>}
      </div>

      {data.updated_at != null && (
        <span className="text-[0.78rem] text-text-muted">
          Last updated {fmtRelative(data.updated_at)}
          {data.updated_by_name ? ` by ${data.updated_by_name}` : ''}
        </span>
      )}
    </div>
  )
}
