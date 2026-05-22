import type { CSSProperties } from 'react'
import { Routes, Route, Outlet, NavLink } from 'react-router-dom'
import HomePage from './pages/HomePage'
import CharacterPage from './pages/CharacterPage'
import ClaimPage from './pages/ClaimPage'
import AdminPage from './pages/AdminPage'
import GuildPage from './pages/GuildPage'
import ItemPage from './pages/ItemPage'
import CharacterSearchPage from './pages/CharacterSearchPage'
import ItemSearchPage from './pages/ItemSearchPage'
import { GuildSearchPage } from './pages/SearchPage'
import UserWidget from './components/UserWidget'
import { useAuth } from './hooks/useAuth'
import { Link } from 'react-router-dom'
import logo from './L&L.png'

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
      <NavLink to="/characters" style={navLinkStyle}>Character</NavLink>
      <NavLink to="/guilds"     style={navLinkStyle}>Guild</NavLink>
      <NavLink to="/items"      style={navLinkStyle}>Item</NavLink>
    </nav>
  )
}

function Layout() {
  const auth = useAuth()

  if (auth.status === 'loading') return null

  if (auth.status === 'unauthenticated') return <LoginGate />

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
        <UserWidget />
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
