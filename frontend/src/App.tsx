import { lazy, Suspense } from 'react'
import { Routes, Route, Outlet, NavLink, useLocation } from 'react-router-dom'
import { DiscordButton } from './components/ui/DiscordButton'
import HomePage from './pages/HomePage'
import SupportPage from './pages/SupportPage'
import CharacterPage from './pages/CharacterPage'
import ClaimPage from './pages/ClaimPage'
import GuildPage from './pages/GuildPage'
import ItemPage from './pages/ItemPage'
import ItemSearchPage from './pages/ItemSearchPage'
import RankingsPage from './pages/RankingsPage'
import RecipesPage from './pages/RecipesPage'
import { CharacterSearchPage, GuildSearchPage } from './pages/SearchPage'
import UserWidget from './components/UserWidget'
import NotFoundPage from './pages/NotFoundPage'
import NotificationBell from './components/NotificationBell'
import RaidingLiveWidget from './components/RaidingLiveWidget'

// Lazy-loaded: low-traffic pages or heavy-deps pages (admin, parse detail, raid editor).
// Each becomes a separate chunk fetched on first navigation.
const AdminPage         = lazy(() => import('./pages/AdminPage'))
const TokensPage        = lazy(() => import('./pages/TokensPage'))
const RolesSettingsPage = lazy(() => import('./pages/RolesSettingsPage'))
const ParsePage         = lazy(() => import('./pages/ParsePage'))
const ParsesPage        = lazy(() => import('./pages/ParsesPage'))
const RaidZonePage      = lazy(() => import('./pages/RaidZonePage'))
const RaidZonesPage     = lazy(() => import('./pages/RaidZonesPage'))
const ComparePage       = lazy(() => import('./pages/ComparePage'))
import { useAuth } from './hooks/useAuth'
import { CensusStreamProvider } from './hooks/useCensusStream'
import { ServerProvider } from './hooks/useServer'
import { Link } from 'react-router-dom'
import logo from './assets/EQ2L.webp'
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
      <DiscordButton />
    </main>
  )
}

/**
 * Stable portion of the pathname used to key the page-enter wrapper.
 *
 * For most routes this is just the pathname — every URL change replays the
 * fade-up entrance and resets in-page state.
 *
 * For `/raids/<zone>/<bossName>` the `:bossName` segment drives the sidebar
 * boss selection. Including it in the key would remount RaidZonePage on
 * every sidebar click (scroll reset, fetch re-runs, animation replay). The
 * regex below collapses the boss-name segment out so the same key is returned
 * for every boss within a zone — React Router still updates `useParams` so
 * the selected encounter changes, but the surrounding chrome stays mounted.
 */
function stablePathKey(pathname: string): string {
  const m = pathname.match(/^(\/raids\/[^/]+)/)
  return m ? m[1] : pathname
}

const navLinkStyle = ({ isActive }: { isActive: boolean }) => ({
  color: isActive ? 'var(--gold-bright)' : 'var(--gold-dim)',
  borderBottom: isActive ? '1px solid var(--gold)' : '1px solid transparent',
})

/**
 * Partner / affiliate site logos, shown in the header next to the main logo.
 * Each links out (new tab) to the partner site. The logo images live in
 * public/ (drop the two files in with these names). Hidden below md so they
 * don't crowd the mobile header.
 */
const PARTNERS = [
  { href: 'https://at-age-s-end.web.app/?utm_source=eq2lexicon', src: '/partner-aae.webp', label: "At Age's End" },
  { href: 'https://eq2tleraid.com/?utm_source=eq2lexicon', src: '/partner-eq2tleraid.webp', label: 'EQ2 TLE Raid' },
  { href: 'https://eq2broker.com/?utm_source=eq2lexicon', src: '/partner-eq2broker.webp', label: 'EQ2 Broker' },
]

function PartnerLinks() {
  return (
    <div className="hidden md:flex items-center gap-2 ml-3 pl-3 border-l border-border">
      {PARTNERS.map(p => (
        <a
          key={p.href}
          href={p.href}
          target="_blank"
          rel="noopener noreferrer"
          title={p.label}
          className="flex items-center h-8 shrink-0 transition-[transform,filter] duration-150 hover:brightness-110 hover:scale-[1.04]"
        >
          <img src={p.src} alt={p.label} className="h-full w-auto" />
        </a>
      ))}
    </div>
  )
}

function NavItem({ to, label, also }: { to: string; label: string; also?: string }) {
  const { pathname } = useLocation()
  const isActive = pathname === to || (also ? pathname.startsWith(also) : false)
  return (
    <NavLink
      to={to}
      end
      className="font-heading text-[0.82rem] font-semibold tracking-[0.07em] no-underline pb-[2px] transition-[color,border-color] duration-150 whitespace-nowrap"
      style={() => navLinkStyle({ isActive })}
    >
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
      <div className="fixed top-0 left-0 right-0 z-[200] flex items-center justify-between py-1.5 px-5 bg-bg/75 backdrop-blur-md border-b border-border">
        <div className="flex items-center">
          <Link to="/" className="flex items-center leading-none">
            <img src={logo} alt="EQ2 Lexicon" className="h-10 w-auto" />
          </Link>
          <PartnerLinks />
        </div>
        {/* Inline nav: lg+ only. Below lg, MobileNav renders the hamburger. */}
        <div className="hidden lg:block">
          <NavLinks />
        </div>
        <div className="flex items-center gap-2.5">
          {/* ACT download icon: lg+ only (it's also in the MobileNav drawer). */}
          {/* The wrapper pins the rendered height to match the
              UserWidget button next to it (Tailwind h-11 = 44px); the
              <img> fills that height with h-full so its top and bottom
              align with the user dropdown's. block-display + items-
              center handle the rare case where the PNG has internal
              transparent padding above/below the visible badge. */}
          <a
            href="https://github.com/VortexUK/EQ2LexiconACTPlugin/releases/latest"
            target="_blank"
            rel="noopener noreferrer"
            title="Download the EQ2 Lexicon ACT plugin"
            className="hidden lg:flex h-11 items-center shrink-0 transition-[transform,filter] duration-150 hover:brightness-110 hover:scale-[1.03]"
          >
            <img src="/download_plugin.webp" alt="Download ACT Plugin" className="h-full w-auto" />
          </a>
          <RaidingLiveWidget />
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
          <Suspense fallback={<div className="p-8 text-text-muted">Loading…</div>}>
            <Outlet />
          </Suspense>
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
          <span>
            <Link
              to="/support"
              className="text-[color:inherit] underline underline-offset-[3px] inline-block py-1 -my-1"
            >
              Support the site
            </Link>
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
        <Route path="/compare" element={<ComparePage />} />
        <Route path="/guild/:guildName" element={<GuildPage />} />
        <Route path="/item/:itemId"    element={<ItemPage />} />
        <Route path="/claim" element={<ClaimPage />} />
        <Route path="/admin"   element={<AdminPage />} />
        <Route path="/recipes" element={<RecipesPage />} />
        <Route path="/raids"                    element={<RaidZonesPage />} />
        <Route path="/raids/:name"              element={<RaidZonePage />} />
        <Route path="/raids/:name/:bossName"    element={<RaidZonePage />} />
        <Route path="/parses"      element={<ParsesPage />} />
        <Route path="/rankings"    element={<RankingsPage />} />
        <Route path="/parse/:id"   element={<ParsePage />} />
        <Route path="/settings/tokens" element={<TokensPage />} />
        <Route path="/settings/roles" element={<RolesSettingsPage />} />
        <Route path="/support" element={<SupportPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
    </CensusStreamProvider>
    </ServerProvider>
  )
}

export default App
