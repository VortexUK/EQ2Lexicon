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
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-2 bg-surface border border-border rounded-md py-[0.35rem] pr-[0.7rem] pl-[0.45rem] cursor-pointer text-text"
      >
        <img
          src={avatarUrl(user)}
          alt="avatar"
          className="w-7 h-7 rounded-full shrink-0"
        />
        <span className="text-[0.9rem] max-w-[140px] overflow-hidden text-ellipsis whitespace-nowrap">
          {user.global_name ?? user.username}
        </span>
        <span className="text-[0.65rem] text-text-muted ml-0.5">
          {open ? '▴' : '▾'}
        </span>
      </button>

      {open && (
        <div
          className="absolute left-0 bg-surface-raised border border-border rounded-md min-w-[160px] z-[100] overflow-hidden"
          style={{ top: 'calc(100% + 6px)', boxShadow: '0 8px 24px rgba(0,0,0,0.4)' }}
        >
          <Link
            to="/claim"
            onClick={() => setOpen(false)}
            className="block py-[0.6rem] px-4 text-text-muted text-[0.88rem] no-underline border-b border-border"
          >
            ★ My Characters
          </Link>
          <Link
            to="/settings/tokens"
            onClick={() => setOpen(false)}
            className="block py-[0.6rem] px-4 text-text-muted text-[0.88rem] no-underline border-b border-border"
          >
            ⚿ API Tokens
          </Link>
          <Link
            to="/settings/roles"
            onClick={() => setOpen(false)}
            className="block py-[0.6rem] px-4 text-text-muted text-[0.88rem] no-underline border-b border-border"
          >
            ✦ Roles
          </Link>
          {user.is_admin && (
            <Link
              to="/admin"
              onClick={() => setOpen(false)}
              className="block py-[0.6rem] px-4 text-text-muted text-[0.88rem] no-underline border-b border-border"
            >
              ⚙ Admin panel
            </Link>
          )}
          <button
            onClick={doLogout}
            className="block w-full text-left py-[0.6rem] px-4 bg-transparent border-none text-danger text-[0.88rem] cursor-pointer"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}
