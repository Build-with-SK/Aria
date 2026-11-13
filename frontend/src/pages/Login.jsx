/**
 * pages/Login.jsx
 * Owner access. The only way into ARIA.
 *
 *   ARIA · Owner access · [ Owner token ] · [ Sign in ]
 *
 * Deliberately nothing else: no registration, no email, no social login, no
 * recovery flow, no animation. SENTINEL's page is the same form in its own
 * colours — one authentication experience across both applications.
 *
 * The token is posted ONCE to /api/auth/login. What comes back is an HttpOnly
 * session cookie this page cannot read; the token itself is never stored in
 * the browser, never put in a URL, and never sent again.
 *
 * Any wrong token gets the same message — nothing here may hint at how close a
 * guess was.
 */
import React, { useEffect, useState } from 'react'
import axios from 'axios'
import { nextAfterLogin } from '../core/appPath'
import { Mark } from '../components/Navigation'

export default function Login() {
  const [token, setToken] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [uninitialised, setUninitialised] = useState(false)

  useEffect(() => {
    // Only to say "not configured" instead of rejecting every attempt as wrong.
    axios.get('/api/auth/status')
      .then(r => setUninitialised(r.data?.authentication_configured === false))
      .catch(() => {})
  }, [])

  // A router path ('/brain'); nextAfterLogin adds the /app base and refuses
  // anything outside the app.
  const next = nextAfterLogin(new URLSearchParams(location.search).get('next') || '/')

  const submit = async e => {
    e.preventDefault()
    if (!token) return
    setErr(''); setBusy(true)
    try {
      await axios.post('/api/auth/login', { token })
      window.location.href = next
    } catch (e) {
      const r = e?.response
      setToken('')
      setErr(r?.status === 401 ? 'Invalid owner token.'
        : r?.status === 429 ? `Too many attempts. Try again in ${r.data?.retry_after || 60} seconds.`
        : r?.status === 503 ? 'Server not initialized: no owner token is configured.'
        : 'Could not reach ARIA.')
      setBusy(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', padding: '24px 16px',
    }}>
      <form onSubmit={submit} className="sheet" style={{ width: '100%', maxWidth: 380, padding: '34px 28px 26px', textAlign: 'center' }}>
        <div style={{ color: 'var(--accent)', display: 'grid', placeItems: 'center' }}><Mark size={52} /></div>
        <h1 style={{ fontSize: 26, fontWeight: 700, margin: '16px 0 0' }}>ARIA</h1>
        <div style={{ fontSize: 15, color: 'var(--ink-2)', margin: '4px 0 24px' }}>Owner access</div>

        {uninitialised && (
          <div role="alert" style={{ fontSize: 13.5, color: 'var(--red)', background: 'var(--red-bg)', borderRadius: 12, padding: '10px 12px', marginBottom: 14 }}>
            Server not initialized: no owner token is configured.
          </div>
        )}

        <input
          type="password" autoComplete="current-password" autoFocus
          aria-label="Owner token" placeholder="Owner token"
          value={token} onChange={e => setToken(e.target.value)} disabled={busy}
          className="aria-input" style={{ textAlign: 'center', minHeight: 48 }}
        />
        <button type="submit" disabled={busy || !token} className="aria-btn primary"
          style={{ width: '100%', marginTop: 12, minHeight: 48, fontSize: 16 }}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>

        <div role="alert" style={{ fontSize: 13.5, color: 'var(--red)', minHeight: 20, marginTop: 12 }}>{err}</div>
        <div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 6, lineHeight: 1.6 }}>
          The token is sent once and is never stored in this browser.
        </div>
      </form>
    </div>
  )
}
