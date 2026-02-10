import { useState, useEffect, useRef } from 'react'
import './LoginPage.css'

export default function LoginPage({ onLogin }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const googleButtonRef = useRef(null)

  useEffect(() => {
    // Fetch Google Client ID from backend, then load Google SDK
    fetch('/api/auth/config')
      .then(res => res.json())
      .then(config => {
        if (!config.google_client_id) {
          setError('Google OAuth not configured')
          return
        }

        // Load Google Identity Services SDK
        const script = document.createElement('script')
        script.src = 'https://accounts.google.com/gsi/client'
        script.async = true
        script.defer = true

        script.onload = () => {
          if (window.google) {
            window.google.accounts.id.initialize({
              client_id: config.google_client_id,
              callback: handleGoogleResponse
            })

            if (googleButtonRef.current) {
              window.google.accounts.id.renderButton(
                googleButtonRef.current,
                {
                  theme: 'filled_blue',
                  size: 'large',
                  text: 'signin_with',
                  width: 300
                }
              )
            }
          }
        }

        document.head.appendChild(script)

        return () => {
          if (script.parentNode) {
            script.parentNode.removeChild(script)
          }
        }
      })
      .catch(() => setError('Failed to load auth config'))
  }, [])

  const handleGoogleResponse = async (response) => {
    if (!response.credential) {
      setError('Google sign-in failed. Please try again.')
      return
    }

    setLoading(true)
    setError('')

    try {
      const res = await fetch('/api/auth/google', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: response.credential })
      })

      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.detail || 'Authentication failed')
      }

      const data = await res.json()
      onLogin(data.token, data.user)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1><span className="login-accent">DeFi</span> Wallet Monitor</h1>

        <p className="login-description">
          Sign in with your Google account to access the application
        </p>

        <div className="google-button-container">
          <div ref={googleButtonRef}></div>
        </div>

        {loading && <div className="login-loading">Signing in...</div>}
        {error && <div className="login-error">{error}</div>}
      </div>
    </div>
  )
}
