/**
 * pages/Login.jsx
 * Owner access. The only way into ARIA.
 *
 *   ARIA · Owner Access · [ Owner Token ] · [ LOGIN ]
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

const mono = { fontFamily: 'var(--mono)' }

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

  const next = new URLSearchParams(location.search).get('next') || '/'

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
      justifyContent: 'center', padding: '24px 16px', background: '#050206',
    }}>
      <form onSubmit={submit} style={{ width: '100%', maxWidth: 320, textAlign: 'center' }}>
        <div style={{ ...mono, fontSize: 22, fontWeight: 800, color: 'var(--orange)',
                      letterSpacing: '0.3em' }}>ARIA</div>
        <div style={{ ...mono, fontSize: 11, color: 'var(--muted)', letterSpacing: '0.14em',
                      margin: '8px 0 22px' }}>Owner Access</div>

        {uninitialised && (
          <div role="alert" style={{ ...mono, fontSize: 11, color: 'var(--red)', marginBottom: 14 }}>
            Server not initialized: no owner token is configured.
          </div>
        )}

        <input
          type="password" autoComplete="current-password" autoFocus
          aria-label="Owner Token" placeholder="Owner Token"
          value={token} onChange={e => setToken(e.target.value)} disabled={busy}
          style={{
            ...mono, width: '100%', boxSizing: 'border-box', padding: '11px 12px',
            fontSize: 13, color: '#e8e8e8', background: '#121016',
            border: '1px solid #2a2630', borderRadius: 4, textAlign: 'center',
          }}
        />
        <button type="submit" disabled={busy || !token}
          style={{
            ...mono, width: '100%', marginTop: 10, padding: '11px 12px',
            background: busy || !token ? '#1a1620' : 'var(--orange)',
            color: busy || !token ? 'var(--muted)' : '#140407',
            border: 'none', borderRadius: 4, fontSize: 12, fontWeight: 700,
            letterSpacing: '0.2em', cursor: busy || !token ? 'default' : 'pointer',
          }}>
          {busy ? '…' : 'LOGIN'}
        </button>

        <div role="alert" style={{ ...mono, fontSize: 11, color: 'var(--red)',
                                   minHeight: 16, marginTop: 12 }}>{err}</div>
      </form>
    </div>
  )
}
