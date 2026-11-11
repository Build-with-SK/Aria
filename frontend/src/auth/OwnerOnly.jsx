/**
 * auth/OwnerOnly.jsx
 * Wraps a route that belongs to the owner alone.
 *
 * The rail already hides these, but a URL can still be typed, bookmarked or
 * shared. Without this the page mounts, fires its requests, collects a fistful
 * of 403s and renders as a broken dashboard — which reads as "ARIA is broken"
 * rather than "this isn't yours". One honest panel is better.
 *
 * This is presentation. The server refuses these routes regardless of what the
 * browser believes, so bypassing this component buys nothing but a blank page.
 */
import React from 'react'
import { useAuth } from './AuthContext'

const mono = { fontFamily: 'var(--mono)' }

export default function OwnerOnly({ children, what = 'This section' }) {
  const { owner, logout } = useAuth()
  if (owner) return children

  // Since the FREE role went, the only session the server issues is the
  // owner's, so this renders only if the two disagree. The old copy promised
  // a non-owner "the 41-module research engine" — which the server refuses
  // them — so it now says what is true and offers the one way forward.
  const signIn = async () => {
    try { await logout() } finally { window.location.href = '/app/login' }
  }

  return (
    <div style={{ maxWidth: 560, margin: '8vh auto 0', padding: '0 16px' }}>
      <div style={{
        background: '#0b080c', border: '1px solid var(--border)',
        borderRadius: 5, padding: '24px 22px',
      }}>
        <div style={{ ...mono, fontSize: 10, color: 'var(--orange)', letterSpacing: '0.18em' }}>
          OWNER ONLY
        </div>
        <h1 style={{ ...mono, fontSize: 15, color: '#e8e8e8', margin: '10px 0 0', fontWeight: 700 }}>
          {what} requires owner access.
        </h1>
        <p style={{ ...mono, fontSize: 11.5, color: 'var(--muted)', lineHeight: 1.7, margin: '12px 0 0' }}>
          This session is not signed in as the owner. Positions, the trading
          desk, ARIA’s private memory and the track record open only to an
          owner session.
        </p>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 18 }}>
          <button onClick={signIn} style={btn(true)}>SIGN IN AS OWNER</button>
        </div>
      </div>
    </div>
  )
}

const btn = primary => ({
  ...mono, fontSize: 11, fontWeight: 700, letterSpacing: '0.06em',
  padding: '9px 14px', borderRadius: 3, textDecoration: 'none', cursor: 'pointer',
  background: primary ? 'var(--orange)' : 'transparent',
  color: primary ? '#000' : 'var(--text-dim)',
  border: `1px solid ${primary ? 'var(--orange)' : 'var(--border)'}`,
})
