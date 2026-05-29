import type { CSSProperties } from 'react'
import { Routes, Route, Outlet, NavLink, useLocation } from 'react-router-dom'
import HomePage from './pages/HomePage'
import CharacterPage from './pages/CharacterPage'
import ClaimPage from './pages/ClaimPage'
import AdminPage from './pages/AdminPage'
import GuildPage from './pages/GuildPage'
import ItemPage from './pages/ItemPage'
import ItemSearchPage from './pages/ItemSearchPage'
import ParsePage from './pages/ParsePage'
import ParsesPage from './pages/ParsesPage'
import RaidZonePage from './pages/RaidZonePage'
import RaidZonesPage from './pages/RaidZonesPage'
import RankingsPage from './pages/RankingsPage'
import RecipesPage from './pages/RecipesPage'
import RolesSettingsPage from './pages/RolesSettingsPage'
import TokensPage from './pages/TokensPage'
import { CharacterSearchPage, GuildSearchPage } from './pages/SearchPage'
import UserWidget from './components/UserWidget'
import NotFoundPage from './pages/NotFoundPage'
import NotificationBell from './components/NotificationBell'
import { useAuth } from './hooks/useAuth'
import { CensusStreamProvider } from './hooks/useCensusStream'
import { ServerProvider, useServer } from './hooks/useServer'
import { Link } from 'react-router-dom'
import logo from './assets/EQ2L.png'
import ServerLaunchTimer from './components/ServerLaunchTimer'
import CensusStatus from './components/CensusStatus'
import { MobileNav } from './components/MobileNav'

function LoginGate() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-6 p-8 text-center">
      <img
        src={logo}
        alt="EQ2 Lexicon"
        className="w-full max-w-[420px]"
        style={{
          WebkitMaskImage: 'linear-gradient(to bottom, black 60%, transparent 100%)',
          maskImage:        'linear-gradient(to bottom, black 60%, transparent 100%)',
        }}
      />
      <p className="text-text-muted text-[0.95rem] max-w-[340px]">
        Sign in with Discord to access your EQ2 Lexicon.
      </p>
      <ServerLaunchTimer />
      <a
        href="/api/auth/login"
        style={{
          display: 'inline-block',
          padding: '0.6rem 1.6rem',
          background: 'var(--discord-brand)',
          color: '#fff',
          borderRadius: 8,
          border: 'none',
          fontSize: '1rem',
          fontWeight: 600,
          textDecoration: 'none',
          letterSpacing: '0.02em',
        }}
      >
        Sign in with Discord
      </a>
    </main>
  )
}

/**
 * Stable portion of the pathname used to key the page-enter wrapper.
 *
 * For most routes this is just the pathname — every URL change replays the
 * fade-up entrance and resets in-page state.
 *
 * For `/raids/<zone>/<position>` the `:position` segment drives the sidebar
 * boss selection. Including it in the key would remount RaidZonePage on
 * every sidebar click (scroll reset, fetch re-runs, animation replay). The
 * regex below collapses the position out so the same key is returned for
 * every boss within a zone — React Router still updates `useParams` so the
 * selected encounter changes, but the surrounding chrome stays mounted.
 */
function stablePathKey(pathname: string): string {
  const m = pathname.match(/^(\/raids\/[^/]+)/)
  return m ? m[1] : pathname
}

const navLinkStyle = ({ isActive }: { isActive: boolean }): CSSProperties => ({
  fontFamily: "var(--font-heading)",
  fontSize: '0.82rem',
  fontWeight: 600,
  letterSpacing: '0.07em',
  textDecoration: 'none',
  color: isActive ? 'var(--gold-bright)' : 'var(--gold-dim)',
  borderBottom: isActive ? '1px solid var(--gold)' : '1px solid transparent',
  paddingBottom: '2px',
  transition: 'color 0.15s, border-color 0.15s',
  whiteSpace: 'nowrap',
})

/**
 * Builds a target URL for switching to another server subdomain.
 * On production (e.g. varsoon.eq2lexicon.com) it swaps the leading subdomain
 * label.  On localhost / any host with no dot it falls back to a plain
 * absolute URL pointing at {subdomain}.eq2lexicon.com so the link is still
 * useful even in dev.
 */
function buildSwitchUrl(targetSubdomain: string, activeSubdomain: string | undefined): string {
  const { protocol, host, pathname, search, hash } = window.location
  // Derive the base domain by stripping the ACTIVE server's subdomain label if
  // the current host carries it. On a subdomain host ("varsoon.eq2lexicon.com",
  // active "varsoon") base = "eq2lexicon.com". On the apex ("eq2lexicon.com",
  // which serves the default server) the host does NOT start with the active
  // subdomain, so base = the host as-is — NOT a blind first-label strip (which
  // turned "eq2lexicon.com" into ".com" → "wuoshi.com").
  let base = host
  if (activeSubdomain && host.toLowerCase().startsWith(`${activeSubdomain.toLowerCase()}.`)) {
    base = host.slice(activeSubdomain.length + 1)
  }
  // localhost / IP without a domain → fall back to the live domain so the link
  // is still useful in dev.
  if (!base.includes('.')) {
    base = 'eq2lexicon.com'
  }
  return `${protocol}//${targetSubdomain}.${base}${pathname}${search}${hash}`
}

/**
 * Server name badge + optional switcher links for other servers.
 * Rendered inside the header next to the logo.
 */
function ServerBadge() {
  const server = useServer()
  if (!server) return null

  const others = server.servers.filter(s => s.world !== server.world)
  const activeSubdomain = server.servers.find(s => s.world === server.world)?.subdomain

  return (
    <div className="flex items-center gap-2 ml-2">
      {/* Active server name */}
      <span
        className="font-heading text-[0.7rem] font-semibold tracking-[0.12em] uppercase px-[0.45rem] py-[0.2rem] rounded-sm"
        style={{
          color: 'var(--gold)',
          background: 'rgba(var(--gold-rgb), 0.1)',
          border: '1px solid rgba(var(--gold-rgb), 0.22)',
        }}
      >
        {server.displayName}
      </span>

      {/* Switch links — only shown when there are other servers */}
      {others.length > 0 && (
        <div className="flex items-center gap-1">
          {others.map(s => (
            <a
              key={s.world}
              href={buildSwitchUrl(s.subdomain, activeSubdomain)}
              className="font-heading text-[0.65rem] font-semibold tracking-[0.1em] uppercase px-[0.4rem] py-[0.18rem] rounded-sm no-underline transition-colors duration-150"
              style={{
                color: 'var(--gold-dim)',
                background: 'transparent',
                border: '1px solid rgba(var(--gold-rgb), 0.15)',
              }}
              onMouseEnter={e => {
                ;(e.currentTarget as HTMLAnchorElement).style.color = 'var(--gold)'
                ;(e.currentTarget as HTMLAnchorElement).style.borderColor = 'rgba(var(--gold-rgb), 0.35)'
              }}
              onMouseLeave={e => {
                ;(e.currentTarget as HTMLAnchorElement).style.color = 'var(--gold-dim)'
                ;(e.currentTarget as HTMLAnchorElement).style.borderColor = 'rgba(var(--gold-rgb), 0.15)'
              }}
              title={`Switch to ${s.displayName}`}
            >
              {s.displayName}
            </a>
          ))}
        </div>
      )}
    </div>
  )
}

function NavItem({ to, label, also }: { to: string; label: string; also?: string }) {
  const { pathname } = useLocation()
  const isActive = pathname === to || (also ? pathname.startsWith(also) : false)
  return (
    <NavLink to={to} end style={() => navLinkStyle({ isActive })}>
      {label}
    </NavLink>
  )
}

function NavLinks() {
  return (
    <nav className="flex items-center gap-5">
      <NavItem to="/"           label="Home" />
      <NavItem to="/characters" label="Characters" also="/character/" />
      <NavItem to="/guilds"     label="Guilds"      also="/guild/" />
      <NavItem to="/items"      label="Items"       also="/item/" />
      <NavItem to="/recipes"    label="Recipes" />
      <NavItem to="/raids"      label="Raids"       also="/raids/" />
      <NavItem to="/parses"     label="Parses"      also="/parse/" />
      <NavItem to="/rankings"   label="Rankings" />
    </nav>
  )
}

function AccessPendingGate() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-4 p-8 text-center">
      <h2 className="font-heading text-[1.8rem] text-gold">
        Access Pending
      </h2>
      <p className="text-text-muted max-w-[360px] leading-relaxed">
        Your account is awaiting approval. An officer will review your request shortly.
      </p>
      <a href="/api/auth/logout" className="text-gold-dim text-[0.85rem]"
        onClick={async e => { e.preventDefault(); await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }); location.href = '/' }}>
        Sign out
      </a>
    </main>
  )
}

function AccessDeniedGate() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-4 p-8 text-center">
      <h2 className="font-heading text-[1.8rem] text-danger">
        Access Denied
      </h2>
      <p className="text-text-muted max-w-[360px] leading-relaxed">
        Your access request was not approved. Contact an officer if you think this is a mistake.
      </p>
      <a href="#" className="text-gold-dim text-[0.85rem]"
        onClick={async e => { e.preventDefault(); await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }); location.href = '/' }}>
        Sign out
      </a>
    </main>
  )
}

function Layout() {
  const auth = useAuth()
  const { pathname } = useLocation()

  if (auth.status === 'loading') return null

  if (auth.status === 'unauthenticated') return <LoginGate />

  if (auth.user.access_status === 'pending')  return <AccessPendingGate />
  if (auth.user.access_status === 'denied')   return <AccessDeniedGate />

  return (
    <>
      <div className="fixed top-0 left-0 right-0 z-[200] flex items-center justify-between py-[0.4rem] px-5 bg-bg/75 backdrop-blur-md border-b border-border">
        <div className="flex items-center">
          <Link to="/" className="flex items-center leading-none">
            <img src={logo} alt="EQ2 Lexicon" className="h-10 w-auto" />
          </Link>
          <ServerBadge />
        </div>
        {/* Inline nav: lg+ only. Below lg, MobileNav renders the hamburger. */}
        <div className="hidden lg:block">
          <NavLinks />
        </div>
        <div className="flex items-center gap-[0.6rem]">
          {/* ACT download icon: lg+ only (it's also in the MobileNav drawer). */}
          <a
            href="https://github.com/VortexUK/EQ2LexiconACTPlugin/releases/latest"
            target="_blank"
            rel="noopener noreferrer"
            title="Download the EQ2 Lexicon ACT plugin"
            className="hidden lg:block shrink-0 transition-[transform,filter] duration-150 hover:brightness-110 hover:scale-[1.03]"
          >
            <img src="/download_plugin.png" alt="Download ACT Plugin" className="h-10 w-auto" />
          </a>
          <NotificationBell />
          <UserWidget />
          {/* Hamburger: below lg only. */}
          <div className="lg:hidden">
            <MobileNav />
          </div>
        </div>
      </div>
      {/* pt-14 pushes content below the fixed header; min-h-screen makes the
          wrapper fill the viewport so the footer sits flush against the
          bottom edge (otherwise min-h = 100vh-header left a strip of the
          background image showing below the footer). */}
      <div className="pt-14 flex flex-col min-h-screen">
        {/* key by the *stable* portion of pathname so the fade-up entrance
            replays on real navigation but NOT on in-page URL changes (the
            raid sidebar updates :position to drive boss selection — we
            don't want that to remount the page + reset scroll). */}
        <div className="page-enter flex-1" key={stablePathKey(pathname)}>
          <Outlet />
        </div>
        <footer className="border-t border-border py-2 px-6 flex items-center justify-between flex-wrap gap-x-4 gap-y-1 text-[0.72rem] text-text-muted opacity-70">
          <span>
            © {new Date().getFullYear()}{' '}
            <a
              href="https://github.com/VortexUK"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[color:inherit] underline underline-offset-[3px] inline-block py-1 -my-1"
            >
              VortexUK
            </a>
          </span>
          <span>
            Game data provided by the{' '}
            <a
              href="https://census.daybreakgames.com"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[color:inherit] underline underline-offset-[3px] inline-block py-1 -my-1"
            >
              Daybreak Games Census API
            </a>
          </span>
          <CensusStatus />
        </footer>
      </div>
    </>
  )
}

function App() {
  return (
    <ServerProvider>
    <CensusStreamProvider>
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/characters" element={<CharacterSearchPage />} />
        <Route path="/guilds"     element={<GuildSearchPage />} />
        <Route path="/items"      element={<ItemSearchPage />} />
        <Route path="/character/:name" element={<CharacterPage />} />
        <Route path="/guild/:guildName" element={<GuildPage />} />
        <Route path="/item/:itemId"    element={<ItemPage />} />
        <Route path="/claim" element={<ClaimPage />} />
        <Route path="/admin"   element={<AdminPage />} />
        <Route path="/recipes" element={<RecipesPage />} />
        <Route path="/raids"                    element={<RaidZonesPage />} />
        <Route path="/raids/:name"              element={<RaidZonePage />} />
        <Route path="/raids/:name/:position"    element={<RaidZonePage />} />
        <Route path="/parses"      element={<ParsesPage />} />
        <Route path="/rankings"    element={<RankingsPage />} />
        <Route path="/parse/:id"   element={<ParsePage />} />
        <Route path="/settings/tokens" element={<TokensPage />} />
        <Route path="/settings/roles" element={<RolesSettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
    </CensusStreamProvider>
    </ServerProvider>
  )
}

export default App
