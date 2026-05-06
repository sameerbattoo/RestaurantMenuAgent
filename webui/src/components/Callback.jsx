import { useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { exchangeCode } from '../services/auth'

function Callback({ onAuth }) {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  useEffect(() => {
    const code = searchParams.get('code')
    if (code) {
      exchangeCode(code)
        .then(() => {
          onAuth()
          navigate('/', { replace: true })
        })
        .catch((err) => {
          console.error('Auth failed:', err)
          navigate('/', { replace: true })
        })
    }
  }, [searchParams, navigate, onAuth])

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950">
      <div className="text-center animate-fade-in">
        <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-brand-500 to-brand-600 shadow-lg shadow-brand-500/25 mb-5">
          <span className="text-2xl">🍽️</span>
        </div>
        <div className="flex items-center gap-2 justify-center">
          <div className="flex gap-1">
            <span className="w-2 h-2 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
            <span className="w-2 h-2 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
            <span className="w-2 h-2 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
          </div>
          <p className="text-sm text-gray-500 dark:text-gray-400">Signing you in...</p>
        </div>
      </div>
    </div>
  )
}

export default Callback
