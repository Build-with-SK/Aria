/**
 * pages/Login.jsx
 * The door. Nothing in ARIA is reachable without coming through it.
 *
 * ARIA never sees a password. Each button hands you to the provider, and what
 * comes back is an identity, not a credential — which is the reason to use
 * OAuth rather than to build a login.
 *
 * Providers you have not configured are shown greyed with the reason, rather
 * than hidden or left to fail at the redirect. A dead button that explains
 * itself is worth more than a missing one.
 */
import React, { useEffect, useState } from 'react'
import axios from 'axios'

const mono = { fontFamily: 'var(--mono)' }

const MARKS = {
  google: (
    <svg width="17" height="17" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#4285F4" d="M45.1 24.5c0-1.6-.1-3.2-.4-4.7H24v8.9h11.9c-.5 2.8-2.1 5.1-4.4 6.7v5.5h7.1c4.2-3.8 6.5-9.5 6.5-16.4z" />
      <path fill="#34A853" d="M24 46c5.9 0 10.9-2 14.6-5.3l-7.1-5.5c-2 1.3-4.5 2.1-7.5 2.1-5.8 0-10.7-3.9-12.4-9.1H4.3v5.7C8 41.3 15.4 46 24 46z" />
      <path fill="#FBBC05" d="M11.6 28.2c-.5-1.3-.7-2.7-.7-4.2s.3-2.9.7-4.2v-5.7H4.3C2.8 17 2 20.4 2 24s.8 7 2.3 9.9l7.3-5.7z" />
      <path fill="#EA4335" d="M24 10.7c3.2 0 6.1 1.1 8.4 3.3l6.3-6.3C34.9 4.1 29.9 2 24 2 15.4 2 8 6.7 4.3 14.1l7.3 5.7c1.7-5.2 6.6-9.1 12.4-9.1z" />
    </svg>
  ),
  github: (
    <svg width="17" height="17" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#e6e6e6" d="M12 .5A11.5 11.5 0 0 0 .5 12a11.5 11.5 0 0 0 7.9 10.9c.6.1.8-.2.8-.6v-2c-3.2.7-3.9-1.4-3.9-1.4-.5-1.3-1.3-1.7-1.3-1.7-1-.7.1-.7.1-.7 1.1.1 1.7 1.2 1.7 1.2 1 1.8 2.7 1.3 3.4 1 .1-.7.4-1.3.7-1.6-2.6-.3-5.3-1.3-5.3-5.8 0-1.3.5-2.3 1.2-3.1-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.3 1.2a11.3 11.3 0 0 1 6 0C17.5 4.7 18.5 5 18.5 5c.6 1.6.2 2.8.1 3.1.8.8 1.2 1.8 1.2 3.1 0 4.5-2.7 5.5-5.3 5.8.4.4.8 1.1.8 2.2v3.3c0 .4.2.7.8.6A11.5 11.5 0 0 0 23.5 12 11.5 11.5 0 0 0 12 .5z" />
    </svg>
  ),
  apple: (
    <svg width="17" height="17" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#e6e6e6" d="M16.4 12.8c0-2.5 2-3.7 2.1-3.8-1.2-1.7-3-1.9-3.6-2-1.5-.2-3 .9-3.8.9-.8 0-2-.9-3.3-.8-1.7 0-3.2 1-4.1 2.5-1.7 3-.4 7.4 1.3 9.8.8 1.2 1.8 2.5 3.1 2.4 1.2 0 1.7-.8 3.2-.8s1.9.8 3.2.8c1.3 0 2.2-1.2 3-2.4.9-1.4 1.3-2.7 1.3-2.8-.1 0-2.4-1-2.4-3.8zM14 5.4c.7-.8 1.1-2 1-3.1-1 0-2.2.7-2.9 1.5-.6.7-1.2 1.9-1 3 1.1.1 2.2-.6 2.9-1.4z" />
    </svg>
  ),
}

const ERRORS = {
  bad_state: 'That sign-in link expired or did not check out. Please try again.',
  exchange_failed: 'The provider would not confirm the sign-in. Please try again.',
  no_code: 'The provider did not send anything back.',
  access_denied: 'Sign-in was cancelled.',
}

export default function Login() {
  const [providers, setProviders] = useState(null)
  const [err, setErr] = useState('')
  const [down, setDown] = useState(false)

  useEffect(() => {
    const q = new URLSearchParams(location.search).get('error')
    if (q) setErr(ERRORS[q] || `Sign-in failed (${q}).`)
    axios.get('/api/auth/providers')
      .then(r => setProviders(r.data?.providers || []))
      .catch(() => setDown(true))
  }, [])

  const next = new URLSearchParams(location.search).get('next') || '/'
  const go = id => { window.location.href = `/api/auth/login/${id}?next=${encodeURIComponent(next)}` }

  const available = (providers || []).filter(p => p.available)
  const missing = (providers || []).filter(p => !p.available)

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: '32px 16px',
      background: 'radial-gradient(1200px 700px at 50% -10%, rgba(255,36,71,0.10), transparent 60%), #050206',
    }}>
      <div style={{ width: '100%', maxWidth: 400 }}>

        {/* wordmark + core */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 26 }}>
          <div style={{ position: 'relative', width: 40, height: 40, flexShrink: 0 }}>
            <div style={{
              position: 'absolute', inset: 0, borderRadius: '50%',
              border: '1px solid rgba(255,36,71,.45)', animation: 'corePulse 2.6s ease-in-out infinite',
            }} />
            <div style={{
              position: 'absolute', inset: 8, borderRadius: '50%',
              border: '1px solid rgba(255,36,71,.30)', animation: 'corePulse 2.6s .4s ease-in-out infinite',
            }} />
            <div style={{
              position: 'absolute', inset: 15, borderRadius: '50%',
              background: 'var(--orange)', boxShadow: 'var(--glow)',
              animation: 'corePulse 2.6s .8s ease-in-out infinite',
            }} />
          </div>
          <div>
            <div style={{
              ...mono, fontSize: 22, fontWeight: 800, color: 'var(--orange)',
              letterSpacing: '0.22em', textShadow: '0 0 16px rgba(255,36,71,.55)',
            }}>ARIA</div>
            <div style={{ ...mono, fontSize: 8, color: 'var(--muted)', letterSpacing: '0.18em', marginTop: 2 }}>
              AUTONOMOUS RESEARCH &amp; INVESTMENT ARCHITECT
            </div>
          </div>
        </div>

        <div style={{
          background: '#0b080c', border: '1px solid var(--border)', borderRadius: 5,
          padding: '22px 20px',
        }}>
          <h1 style={{ ...mono, fontSize: 13, fontWeight: 700, color: '#e8e8e8', letterSpacing: '0.10em', margin: 0 }}>
            SIGN IN
          </h1>
          <p style={{ ...mono, fontSize: 11, color: 'var(--muted)', lineHeight: 1.65, margin: '10px 0 18px' }}>
            ARIA never sees a password. You sign in with a provider you already
            trust, and it tells us only who you are.
          </p>

          {err && (
            <div role="alert" style={{
              ...mono, fontSize: 11, color: 'var(--red)', background: 'rgba(255,77,109,0.07)',
              border: '1px solid rgba(255,77,109,0.35)', borderRadius: 3,
              padding: '9px 11px', marginBottom: 14, lineHeight: 1.5,
            }}>{err}</div>
          )}

          {down && (
            <div role="alert" style={{
              ...mono, fontSize: 11, color: 'var(--muted)', border: '1px solid var(--border)',
              borderRadius: 3, padding: '9px 11px', marginBottom: 14, lineHeight: 1.5,
            }}>
              Can’t reach ARIA’s API. If you are running it locally, start the
              backend on port 8000 and reload.
            </div>
          )}

          {providers === null && !down && (
            <div style={{ ...mono, fontSize: 11, color: 'var(--muted)' }}>checking sign-in options…</div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
            {available.map(p => (
              <button key={p.id} onClick={() => go(p.id)}
                style={{
                  ...mono, display: 'flex', alignItems: 'center', gap: 11, width: '100%',
                  padding: '11px 13px', cursor: 'pointer',
                  background: '#121016', color: '#e8e8e8',
                  border: '1px solid #2a2630', borderRadius: 4,
                  fontSize: 12, fontWeight: 600, letterSpacing: '0.04em', textAlign: 'left',
                }}>
                {MARKS[p.id]}
                <span style={{ flex: 1 }}>Continue with {p.label}</span>
                <span aria-hidden="true" style={{ color: 'var(--muted)' }}>›</span>
              </button>
            ))}
          </div>

          {providers !== null && available.length === 0 && !down && (
            <div style={{
              ...mono, fontSize: 11, color: 'var(--muted)', lineHeight: 1.65,
              border: '1px solid var(--border)', borderRadius: 3, padding: '11px 12px',
            }}>
              <strong style={{ color: 'var(--orange)' }}>No sign-in provider is configured yet.</strong>
              <div style={{ marginTop: 7 }}>
                Register an OAuth app with a provider, then put its client ID and
                secret in <code>.env</code> and restart the backend.
              </div>
            </div>
          )}

          {missing.length > 0 && available.length > 0 && (
            <details style={{ marginTop: 14 }}>
              <summary style={{ ...mono, fontSize: 10, color: 'var(--muted)', cursor: 'pointer', letterSpacing: '0.06em' }}>
                {missing.length} more not configured
              </summary>
              <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
                {missing.map(p => (
                  <div key={p.id} style={{
                    ...mono, fontSize: 10, color: 'var(--muted)', lineHeight: 1.5,
                    border: '1px dashed #26222c', borderRadius: 3, padding: '8px 10px',
                  }}>
                    <strong style={{ color: '#7e7688' }}>{p.label}</strong> — {p.reason}
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>

        <p style={{ ...mono, fontSize: 9.5, color: 'var(--muted)', lineHeight: 1.7, marginTop: 16, textAlign: 'center' }}>
          Research and education only. ARIA is not a licensed adviser and does
          not give personalised financial advice.
        </p>
      </div>
    </div>
  )
}
