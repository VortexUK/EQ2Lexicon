import { Routes, Route } from 'react-router-dom'
import HomePage from './pages/HomePage'
import CharacterPage from './pages/CharacterPage'
import ClaimPage from './pages/ClaimPage'
import AdminPage from './pages/AdminPage'

function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/character/:name" element={<CharacterPage />} />
      <Route path="/claim" element={<ClaimPage />} />
      <Route path="/admin" element={<AdminPage />} />
    </Routes>
  )
}

export default App
