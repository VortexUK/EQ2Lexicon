import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { avatarUrl, useAuth } from '../hooks/useAuth'

async function doLogout() {
  await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
  window.location.reload()
}

export default function UserWidget() {
  const auth = useAuth()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  if (auth.status === 'loading') return null

  if (auth.status === 'unauthenticated') {
    return (
      <a
        href="/api/auth/login"
        style={{
          display: 'inline-block',
          padding: '0.4rem 1rem',
          background: 'var(--discord-brand)',
          color: 'var(--text)',
          borderRadius: 6,
          border: '1px solid var(--border)',
          cursor: 'pointer',
          fontSize: '0.88rem',
          whiteSpace: 'nowrap',
          textDecoration: 'none',
        }}
      >
        Sign in with Discord
      </a>
    )
  }

  const { user } = auth

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: '0.5rem',
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 8, padding: '0.35rem 0.7rem 0.35rem 0.45rem',
          cursor: 'pointer', color: 'var(--text)',
        }}
      >
        <img
          src={avatarUrl(user)}
          alt="avatar"
          style={{ width: 28, height: 28, borderRadius: '50%', flexShrink: 0 }}
        />
        <span style={{ fontSize: '0.9rem', maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {user.global_name ?? user.username}
        </span>
        <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginLeft: 2 }}>
          {open ? '▴' : '▾'}
        </span>
      </button>

      {open && (
        <div style={{
          position: 'absolute', left: 0, top: 'calc(100% + 6px)',
          background: 'var(--surface-raised)', border: '1px solid var(--border)',
          borderRadius: 8, minWidth: 160, zIndex: 100,
          boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
          overflow: 'hidden',
        }}>
          <Link
            to="/claim"
            onClick={() => setOpen(false)}
            style={{
              display: 'block', padding: '0.6rem 1rem',
              color: 'var(--text-muted)', fontSize: '0.88rem',
              textDecoration: 'none', borderBottom: '1px solid var(--border)',
            }}
          >
            ★ My Characters
          </Link>
          <Link
            to="/settings/tokens"
            onClick={() => setOpen(false)}
            style={{
              display: 'block', padding: '0.6rem 1rem',
              color: 'var(--text-muted)', fontSize: '0.88rem',
              textDecoration: 'none', borderBottom: '1px solid var(--border)',
            }}
          >
            ⚿ API Tokens
          </Link>
          {user.is_admin && (
            <Link
              to="/admin"
              onClick={() => setOpen(false)}
              style={{
                display: 'block', padding: '0.6rem 1rem',
                color: 'var(--text-muted)', fontSize: '0.88rem',
                textDecoration: 'none', borderBottom: '1px solid var(--border)',
              }}
            >
              ⚙ Admin panel
            </Link>
          )}
          <button
            onClick={doLogout}
            style={{
              display: 'block', width: '100%', textAlign: 'left',
              padding: '0.6rem 1rem', background: 'transparent',
              border: 'none', color: 'var(--danger)', fontSize: '0.88rem', cursor: 'pointer',
            }}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}
