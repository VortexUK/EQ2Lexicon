/**
 * SiteSettingsSection — site-wide (not per-server) knobs. One today: the
 * "Join our Discord community" invite shown in the footer and on the
 * Support page on every subdomain. Saving refreshes the server bootstrap
 * so the footer updates without a reload.
 */
import { useEffect, useState } from 'react'

import { Button } from '../../components/ui'
import { useRefreshServer, useServer } from '../../hooks/useServer'
import { INPUT_CLS, SECTION_TITLE_CLS } from './types'

export function SiteSettingsSection() {
  const server = useServer()
  const refreshServer = useRefreshServer()
  const [invite, setInvite] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null)

  // The bootstrap may land after mount; seed once it does.
  useEffect(() => {
    setInvite(server?.discordInviteUrl ?? '')
  }, [server?.discordInviteUrl])

  async function handleSave() {
    setBusy(true)
    setResult(null)
    try {
      const res = await fetch('/api/admin/site-settings', {
        method: 'PUT',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ discord_invite_url: invite.trim() || null }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setResult({ ok: false, msg: data.detail ?? 'Save failed.' })
        return
      }
      setResult({ ok: true, msg: invite.trim() ? 'Saved.' : 'Cleared — the link is hidden.' })
      refreshServer()
    } catch {
      setResult({ ok: false, msg: 'Network error — save failed.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <p className={SECTION_TITLE_CLS}>Site settings</p>
      <div className="card">
        <label htmlFor="site-discord-invite" className="block text-[0.72rem] uppercase tracking-[0.06em] text-text-muted font-semibold mb-1">
          Discord invite URL
        </label>
        <input
          id="site-discord-invite"
          type="url"
          value={invite}
          onChange={e => setInvite(e.target.value)}
          placeholder="https://discord.gg/xxxxxxx"
          className={INPUT_CLS}
        />
        <p className="text-[0.7rem] text-text-muted mt-1 italic">
          Shown as &quot;Join our Discord community&quot; in the footer and on the Support page. Leave blank to hide.
          Use a permanent invite: in Discord, Server Settings → Invites (or edit the invite link) → Expire after
          Never, Max uses No limit. The default Invite People link expires after 7 days.
        </p>
        <div className="flex items-center gap-3 mt-3 flex-wrap">
          <Button variant="primary" size="sm" onClick={handleSave} disabled={busy}>
            {busy ? 'Saving…' : 'Save'}
          </Button>
          {result && (
            <span className={`text-[0.82rem] ${result.ok ? 'text-success' : 'text-danger'}`}>
              {result.msg}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
