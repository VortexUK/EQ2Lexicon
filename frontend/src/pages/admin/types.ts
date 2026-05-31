// ── Shared types for the admin sub-tables ─────────────────────────────────────
import { fmtLocalDateTime } from '../../formatters'

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
  /** Soft warnings the plugin (v0.1.15+) attached at upload time —
   *  currently just `"folder_hint_mismatch"`. null when no warnings
   *  on this parse; the admin row renders a ⚠ chip when non-empty.
   *  The hard tamper signals (rename, import) DON'T appear here —
   *  those land in /admin/tamper-reports instead because they
   *  blocked the leaderboard upload entirely. */
  client_warnings: string[] | null
}

// ── Tamper-report audit channel ─────────────────────────────────────────────

/** Reason codes the plugin emits today. Stored as free-form strings
 *  server-side so a future plugin version can add a code without a
 *  server bump — the UI falls back to "unknown" styling in that case.
 *  See web/routes/parses/tamper_report.py:KNOWN_TAMPER_REASONS. */
export type TamperReason =
  | 'title_enemy_mismatch'      // ACT right-click → Rename Encounter
  | 'stale_encounter'           // EndTime > 1 hour ago (almost certainly imported)
  | 'recent_import_activity'    // ACT's ActiveControl was the Import UI within 30s

export interface TamperReport {
  id:                     number
  world:                  string
  act_encid:              string
  title:                  string
  zone:                   string | null
  started_at:             number
  ended_at:               number
  duration_s:             number
  total_damage:           number
  encdps:                 number
  reason:                 string  // intentionally not narrowed — future codes pass through
  reported_at:            number
  uploader_logger_name:   string
  uploader_discord_id:    string
  uploader_discord_name:  string
  guild_name:             string | null
  acknowledged_at:        number | null
  acknowledged_by:        string | null
}

export interface TamperReportListResponse {
  results:       TamperReport[]
  pending_count: number
}

export type TamperReportFilter = 'pending' | 'ack' | 'all'

/** Map a reason code → Badge variant for visual prioritisation.
 *  rename = danger (deliberate misrepresentation, highest signal),
 *  stale / import = warning (suspect, but could be sloppiness),
 *  unknown = muted (future plugin code we don't recognise). */
export const TAMPER_REASON_VARIANT: Record<string, 'danger' | 'warning' | 'muted'> = {
  title_enemy_mismatch:    'danger',
  stale_encounter:         'warning',
  recent_import_activity:  'warning',
}

/** Human-readable label for the admin UI. The server stores the wire
 *  code; the UI presents this. Unknown codes fall back to the raw code. */
export const TAMPER_REASON_LABEL: Record<string, string> = {
  title_enemy_mismatch:    'Rename detected',
  stale_encounter:         'Stale (imported)',
  recent_import_activity:  'Import UI active',
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
export const TH_CLS            = 'text-left px-3 py-2 text-text-muted text-[0.72rem] font-semibold uppercase tracking-[0.05em] border-b border-border whitespace-nowrap'
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
  return fmtLocalDateTime(unix)
}
