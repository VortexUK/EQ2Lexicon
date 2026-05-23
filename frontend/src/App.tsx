import type { CSSProperties } from 'react'
import { Routes, Route, Outlet, NavLink } from 'react-router-dom'
import HomePage from './pages/HomePage'
import CharacterPage from './pages/CharacterPage'
import ClaimPage from './pages/ClaimPage'
import AdminPage from './pages/AdminPage'
import GuildPage from './pages/GuildPage'
import ItemPage from './pages/ItemPage'
import ItemSearchPage from './pages/ItemSearchPage'
import { CharacterSearchPage, GuildSearchPage } from './pages/SearchPage'
import UserWidget from './components/UserWidget'
import NotificationBell from './components/NotificationBell'
import { useAuth } from './hooks/useAuth'
import { Link } from 'react-router-dom'
import logo from './L&L.png'
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
      <h1 style={{
        fontFamily: "'Cinzel', serif",
        fontSize: '2.6rem',
        fontWeight: 700,
        letterSpacing: '0.06em',
        background: 'linear-gradient(135deg, #c8a96e 0%, #e8d5a3 40%, #c8a96e 70%, #a07840 100%)',
        WebkitBackgroundClip: 'text',
        WebkitTextFillColor: 'transparent',
        backgroundClip: 'text',
        display: 'inline-block',
      }}>
        Lore <span style={{ fontWeight: 300, opacity: 0.8 }}>&</span> Legend
      </h1>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.95rem', maxWidth: 340 }}>
        Sign in with Discord to access the guild companion.
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
          <img src={logo} alt="Lore & Legend" style={{ height: 40, width: 'auto' }} />
        </Link>
        <NavLinks />
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <NotificationBell />
          <UserWidget />
        </div>
      </div>
      {/* Push content below fixed header (~52px) */}
      <div style={{ paddingTop: '3.5rem' }}>
        <Outlet />
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
        <Route path="/admin" element={<AdminPage />} />
      </Route>
    </Routes>
  )
}

export default App
