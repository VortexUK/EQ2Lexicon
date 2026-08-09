/**
 * Mobile/tablet nav drawer — shown below `lg:` only (App.tsx hides the
 * inline <NavLinks /> in the same window). Hamburger button anchors at the
 * top-right of the header; tapping opens a full-width overlay with the nav
 * destinations stacked vertically, grouped under the same headings as the
 * desktop dropdowns (Browse / Raids / Leaderboards). Closes on link click,
 * on Escape, and on backdrop tap.
 */
import { useEffect, useRef, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'

type NavSpec = { to: string; label: string; also?: string }
type NavGroup = { heading?: string; items: NavSpec[] }

const GROUPS: NavGroup[] = [
  { items: [{ to: '/', label: 'Home' }] },
  { heading: 'Browse', items: [
    { to: '/characters', label: 'Characters', also: '/character/' },
    { to: '/guilds',     label: 'Guilds',     also: '/guild/' },
    { to: '/items',      label: 'Items',      also: '/item/' },
    { to: '/recipes',    label: 'Recipes' },
  ] },
  { heading: 'Raids', items: [
    { to: '/raids',    label: 'Strategies', also: '/raids/' },
    { to: '/triggers', label: 'Triggers' },
  ] },
  { heading: 'Leaderboards', items: [
    { to: '/parses',   label: 'Parses', also: '/parse/' },
    { to: '/rankings', label: 'Rankings' },
    { to: '/stats',    label: 'Stats' },
  ] },
  { items: [{ to: '/downloads', label: 'Downloads' }] },
]

export function MobileNav() {
  const [open, setOpen] = useState(false)
  const { pathname } = useLocation()
  const firstLinkRef = useRef<HTMLAnchorElement | null>(null)

  // Close on route change so tapping a link snaps the drawer shut.
  useEffect(() => {
    setOpen(false)
  }, [pathname])

  // Esc closes; lock body scroll while open.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    firstLinkRef.current?.focus()
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [open])

  return (
    <>
      <button
        type="button"
        aria-label={open ? 'Close menu' : 'Open menu'}
        aria-expanded={open}
        onClick={() => setOpen(v => !v)}
        className="appearance-none border-0 bg-transparent p-2 cursor-pointer text-gold hover:text-gold-bright"
      >
        {/* simple 3-bar / X icon */}
        <svg width="22" height="22" viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
          {open
            ? <><line x1="4" y1="4" x2="18" y2="18" /><line x1="18" y1="4" x2="4" y2="18" /></>
            : <><line x1="3" y1="6" x2="19" y2="6" /><line x1="3" y1="11" x2="19" y2="11" /><line x1="3" y1="16" x2="19" y2="16" /></>}
        </svg>
      </button>

      {open && (
        <>
          {/* Backdrop: tap to close. z below the panel, above page content. */}
          <div
            className="fixed inset-0 top-14 z-[250] bg-bg/70 backdrop-blur-sm"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          {/* Panel: full-width, slides down from under the header. */}
          <nav
            className="fixed left-0 right-0 top-14 z-[260] bg-bg/95 border-b border-border shadow-2xl flex flex-col py-2 max-h-[calc(100vh-3.5rem)] overflow-y-auto"
            aria-label="Mobile navigation"
          >
            {GROUPS.map((g, gi) => (
              <div key={g.heading ?? `g${gi}`} className={gi > 0 ? 'border-t border-border/60 mt-1 pt-1' : ''}>
                {g.heading && (
                  <div className="px-6 pt-2 pb-1 font-heading text-[0.62rem] uppercase tracking-[0.14em] text-text-muted/70">
                    {g.heading}
                  </div>
                )}
                {g.items.map(it => (
                  <NavLink
                    key={it.to}
                    to={it.to}
                    end
                    ref={it.to === '/' ? firstLinkRef : undefined}
                    className={({ isActive }) => {
                      const active = isActive || (it.also ? pathname.startsWith(it.also) : false)
                      return [
                        'block py-3 px-6 no-underline font-heading text-[0.95rem] tracking-[0.06em]',
                        'border-l-2',
                        active
                          ? 'text-gold-bright border-gold bg-surface/40'
                          : 'text-gold-dim border-transparent hover:text-gold hover:bg-surface/20',
                      ].join(' ')
                    }}
                  >
                    {it.label}
                  </NavLink>
                ))}
              </div>
            ))}
          </nav>
        </>
      )}
    </>
  )
}
