import { Routes, Route } from 'react-router-dom'
import { useState, useEffect } from 'react'
import LoginPage from './components/LoginPage'
import ChatView from './components/ChatView'
import Callback from './components/Callback'
import { getIdToken } from './services/auth'
import { useTheme } from './hooks/useTheme'

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const { theme, toggleTheme } = useTheme()

  useEffect(() => {
    const token = getIdToken()
    setIsAuthenticated(!!token)
  }, [])

  return (
    <Routes>
      <Route
        path="/callback"
        element={<Callback onAuth={() => setIsAuthenticated(true)} />}
      />
      <Route
        path="/*"
        element={
          isAuthenticated ? (
            <ChatView theme={theme} toggleTheme={toggleTheme} />
          ) : (
            <LoginPage theme={theme} toggleTheme={toggleTheme} />
          )
        }
      />
    </Routes>
  )
}

export default App
