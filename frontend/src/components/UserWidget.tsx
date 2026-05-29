import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { avatarUrl, useAuth } from '../hooks/useAuth'
import { DiscordButton } from './ui/DiscordButton'

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
    return <DiscordButton />
  }

  const { user } = auth

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        /* h-11 (44px) matches the Download ACT Plugin badge wrapper in
           App.tsx so the two header-right buttons share top and bottom
           edges. Padding stays — the small extra vertical space the
           explicit height creates centres the 28px avatar nicely. */
        className="flex h-11 items-center gap-2 bg-surface border border-border rounded-md py-[0.35rem] pr-[0.7rem] pl-[0.45rem] cursor-pointer text-text"
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
          className="absolute right-0 bg-surface-raised border border-border rounded-md min-w-[160px] z-dropdown overflow-hidden"
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
