// ── Shared types for the admin sub-tables ─────────────────────────────────────

export interface ServerConfig {
  world:        string
  subdomain:    string
  display_name: string
  max_level:    number
  current_xpac: string | null
  launch_dt:    string | null
  is_default:   boolean
}

export interface ExpansionEntry {
  short: string
  name:  string
}

export interface UserItem {
  discord_id:       string
  discord_name:     string | null
  discord_username: string | null
  avatar:           string | null
  first_seen:       number
  last_seen:        number
  access_status:    string
  claim_count:      number
  /** DB-granted roles (e.g. 'contributor'). Doesn't include 'admin' or
   * 'officer' — see UserResponse.static_roles in useAuth.ts for the
   * trade-off. */
  roles:            string[]
}

export interface ClaimDetail {
  id:               number
  discord_id:       string
  discord_name:     string | null
  discord_username: string | null
  avatar:           string | null
  character_name:   string
  status:           string
  requested_at:     number
  reviewed_at:      number | null
  reviewed_by:      string | null
  note:             string | null
}

export interface AdminParse {
  id:            number
  title:         string
  zone:          string | null
  guild_name:    string | null
  uploaded_by:   string | null
  started_at:    number
  success_level: number   // 1=win, 2=loss, 3=mixed, 0=unknown
  player_count:  number
  hidden:        boolean
}

export interface RoleRequest {
  id:               number
  discord_id:       string
  discord_name:     string | null
  discord_username: string | null
  avatar:           string | null
  role:             string
  status:           'pending' | 'approved' | 'rejected' | 'withdrawn'
  requested_at:     number
  reviewed_at:      number | null
  reviewed_by:      string | null
  user_note:        string | null
  admin_note:       string | null
}

// ── Shared table class strings ────────────────────────────────────────────────

export const SECTION_TITLE_CLS = 'text-[0.8rem] uppercase tracking-[0.07em] text-text-muted mb-3 font-semibold'
export const TABLE_CLS         = 'w-full border-collapse text-[0.875rem]'
export const TH_CLS            = 'text-left px-3 py-[0.45rem] text-text-muted text-[0.72rem] font-semibold uppercase tracking-[0.05em] border-b border-border whitespace-nowrap'
export const TD_CLS            = 'px-3 py-2 border-b border-white/5 align-middle'

/** @deprecated Use ACCESS_BADGE_VARIANT + ui/Badge instead. */
export const ACCESS_BADGE: Record<string, React.CSSProperties> = {
  pending:  { background: 'rgba(var(--warning-rgb), 0.18)',   color: 'var(--warning)', border: '1px solid rgba(var(--warning-rgb), 0.4)'  },
  approved: { background: 'rgba(34,197,94,0.13)',   color: 'var(--success)', border: '1px solid rgba(34,197,94,0.35)' },
  denied:   { background: 'rgba(239,68,68,0.13)',   color: 'var(--danger)', border: '1px solid rgba(239,68,68,0.35)' },
}

/** @deprecated Use CLAIM_BADGE_VARIANT + ui/Badge instead. */
export const CLAIM_BADGE: Record<string, React.CSSProperties> = {
  pending:    { background: 'rgba(var(--warning-rgb), 0.18)',    color: 'var(--warning)', border: '1px solid rgba(var(--warning-rgb), 0.4)'    },
  approved:   { background: 'rgba(34,197,94,0.13)',    color: 'var(--success)', border: '1px solid rgba(34,197,94,0.35)'   },
  rejected:   { background: 'rgba(239,68,68,0.13)',    color: 'var(--danger)', border: '1px solid rgba(239,68,68,0.35)'   },
  withdrawn:  { background: 'rgba(100,116,139,0.13)',  color: '#94a3b8', border: '1px solid rgba(100,116,139,0.3)'  },
  superseded: { background: 'rgba(100,116,139,0.13)',  color: '#94a3b8', border: '1px solid rgba(100,116,139,0.3)'  },
}

export const ACCESS_BADGE_VARIANT: Record<string, 'warning' | 'success' | 'danger'> = {
  pending:  'warning',
  approved: 'success',
  denied:   'danger',
}

export const CLAIM_BADGE_VARIANT: Record<string, 'warning' | 'success' | 'danger' | 'muted'> = {
  pending:    'warning',
  approved:   'success',
  rejected:   'danger',
  withdrawn:  'muted',
  superseded: 'muted',
}

// ── Shared helper ─────────────────────────────────────────────────────────────

export function fmt(unix: number): string {
  return new Date(unix * 1000).toLocaleDateString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
  })
}
