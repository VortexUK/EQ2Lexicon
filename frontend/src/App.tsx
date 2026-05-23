import type { CSSProperties } from 'react'
import { Routes, Route, Outlet, NavLink } from 'react-router-dom'
import HomePage from './pages/HomePage'
import CharacterPage from './pages/CharacterPage'
import ClaimPage from './pages/ClaimPage'
import AdminPage from './pages/AdminPage'
import GuildPage from './pages/GuildPage'
import ItemPage from './pages/ItemPage'
import ItemSearchPage from './pages/ItemSearchPage'
import RecipesPage from './pages/RecipesPage'
import { CharacterSearchPage, GuildSearchPage } from './pages/SearchPage'
import UserWidget from './components/UserWidget'
import NotFoundPage from './pages/NotFoundPage'
import NotificationBell from './components/NotificationBell'
import { useAuth } from './hooks/useAuth'
import { Link } from 'react-router-dom'
import logo from './EQ2L.png'
import ServerLaunchTimer from './components/ServerLaunchTimer'

function LoginGate() {
  return (
    <main style={{
      minHeight: '100vh',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: '1.5rem',
      padding: '2rem',
      textAlign: 'center',
    }}>
      <img
        src="/logo.png"
        alt="EQ2 Lexicon"
        style={{
          width: '100%',
          maxWidth: 420,
          WebkitMaskImage: 'linear-gradient(to bottom, black 60%, transparent 100%)',
          maskImage:        'linear-gradient(to bottom, black 60%, transparent 100%)',
        }}
      />
      <p style={{ color: 'var(--text-muted)', fontSize: '0.95rem', maxWidth: 340 }}>
        Sign in with Discord to access your EQ2 Lexicon.
      </p>
      <ServerLaunchTimer />
      <a
        href="/api/auth/login"
        style={{
          display: 'inline-block',
          padding: '0.6rem 1.6rem',
          background: '#5865F2',
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

const navLinkStyle = ({ isActive }: { isActive: boolean }): CSSProperties => ({
  fontFamily: "'Cinzel', serif",
  fontSize: '0.82rem',
  fontWeight: 600,
  letterSpacing: '0.07em',
  textDecoration: 'none',
  color: isActive ? '#e8d5a3' : '#9a7d4a',
  borderBottom: isActive ? '1px solid #c8a96e' : '1px solid transparent',
  paddingBottom: '2px',
  transition: 'color 0.15s, border-color 0.15s',
  whiteSpace: 'nowrap',
})

function NavLinks() {
  return (
    <nav style={{ display: 'flex', alignItems: 'center', gap: '1.25rem' }}>
      <NavLink to="/" end      style={navLinkStyle}>Home</NavLink>
      <NavLink to="/characters" style={navLinkStyle}>Character</NavLink>
      <NavLink to="/guilds"     style={navLinkStyle}>Guild</NavLink>
      <NavLink to="/items"      style={navLinkStyle}>Item</NavLink>
      <NavLink to="/recipes"    style={navLinkStyle}>Recipes</NavLink>
    </nav>
  )
}

function AccessPendingGate() {
  return (
    <main style={{
      minHeight: '100vh', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      gap: '1rem', padding: '2rem', textAlign: 'center',
    }}>
      <h2 style={{ fontFamily: "'Cinzel', serif", fontSize: '1.8rem', color: '#c8a96e' }}>
        Access Pending
      </h2>
      <p style={{ color: 'var(--text-muted)', maxWidth: 360, lineHeight: 1.6 }}>
        Your account is awaiting approval. An officer will review your request shortly.
      </p>
      <a href="/api/auth/logout" style={{ color: '#9a7d4a', fontSize: '0.85rem' }}
        onClick={async e => { e.preventDefault(); await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }); location.href = '/' }}>
        Sign out
      </a>
    </main>
  )
}

function AccessDeniedGate() {
  return (
    <main style={{
      minHeight: '100vh', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      gap: '1rem', padding: '2rem', textAlign: 'center',
    }}>
      <h2 style={{ fontFamily: "'Cinzel', serif", fontSize: '1.8rem', color: '#f87171' }}>
        Access Denied
      </h2>
      <p style={{ color: 'var(--text-muted)', maxWidth: 360, lineHeight: 1.6 }}>
        Your access request was not approved. Contact an officer if you think this is a mistake.
      </p>
      <a href="#" style={{ color: '#9a7d4a', fontSize: '0.85rem' }}
        onClick={async e => { e.preventDefault(); await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }); location.href = '/' }}>
        Sign out
      </a>
    </main>
  )
}

function Layout() {
  const auth = useAuth()

  if (auth.status === 'loading') return null

  if (auth.status === 'unauthenticated') return <LoginGate />

  if (auth.user.access_status === 'pending')  return <AccessPendingGate />
  if (auth.user.access_status === 'denied')   return <AccessDeniedGate />

  return (
    <>
      <div style={{
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 200,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0.4rem 1.25rem',
        background: 'rgba(15,17,23,0.75)',
        backdropFilter: 'blur(8px)',
        WebkitBackdropFilter: 'blur(8px)',
        borderBottom: '1px solid var(--border)',
      }}>
        <Link to="/" style={{ display: 'flex', alignItems: 'center', lineHeight: 0 }}>
          <img src={logo} alt="EQ2 Lexicon" style={{ height: 40, width: 'auto' }} />
        </Link>
        <NavLinks />
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <NotificationBell />
          <UserWidget />
        </div>
      </div>
      {/* Push content below fixed header (~52px) */}
      <div style={{ paddingTop: '3.5rem', minHeight: 'calc(100vh - 3.5rem)', display: 'flex', flexDirection: 'column' }}>
        <div style={{ flex: 1 }}>
          <Outlet />
        </div>
        <footer style={{
          borderTop: '1px solid var(--border)',
          padding: '1.1rem 1.5rem',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '0.5rem',
          fontSize: '0.72rem',
          color: 'var(--text-muted)',
          opacity: 0.7,
        }}>
          <span>
            © {new Date().getFullYear()}{' '}
            <a
              href="https://github.com/VortexUK"
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: 'inherit', textDecoration: 'underline', textUnderlineOffset: 3 }}
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
              style={{ color: 'inherit', textDecoration: 'underline', textUnderlineOffset: 3 }}
            >
              Daybreak Games Census API
            </a>
          </span>
        </footer>
      </div>
    </>
  )
}

function App() {
  return (
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
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

export default App
