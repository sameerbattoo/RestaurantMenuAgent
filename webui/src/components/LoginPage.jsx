import { login } from '../services/auth'
import ThemeToggle from './ThemeToggle'

function LoginPage({ theme, toggleTheme }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-orange-50 via-white to-amber-50 dark:from-gray-950 dark:via-gray-900 dark:to-gray-950 px-4">
      {/* Theme toggle in corner */}
      <div className="fixed top-5 right-5">
        <ThemeToggle theme={theme} toggleTheme={toggleTheme} />
      </div>

      <div className="w-full max-w-sm animate-fade-in">
        {/* Logo / Brand */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-brand-500 to-brand-600 shadow-lg shadow-brand-500/25 mb-5">
            <span className="text-3xl">🍽️</span>
          </div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">
            Menu Assistant
          </h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">
            AI-powered restaurant menu processing
          </p>
        </div>

        {/* Card */}
        <div className="glass-panel rounded-2xl shadow-xl shadow-gray-200/50 dark:shadow-black/20 p-8">
          <div className="space-y-4">
            <div className="text-center">
              <p className="text-sm text-gray-600 dark:text-gray-300">
                Upload menus, extract items, and manage your restaurant data with AI.
              </p>
            </div>

            <button
              onClick={login}
              className="w-full flex items-center justify-center gap-2 bg-gradient-to-r from-brand-500 to-brand-600 hover:from-brand-600 hover:to-brand-700 text-white font-semibold py-3 px-6 rounded-xl transition-all duration-200 shadow-md shadow-brand-500/25 hover:shadow-lg hover:shadow-brand-500/30 hover:-translate-y-0.5 active:translate-y-0"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
              </svg>
              Sign In
            </button>
          </div>

          <div className="mt-6 pt-5 border-t border-gray-100 dark:border-gray-800">
            <p className="text-xs text-center text-gray-400 dark:text-gray-500">
              Secured with AWS Cognito
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

export default LoginPage
