/**
 * auth/OwnerOnly.jsx
 * Wraps a route that belongs to the owner alone.
 *
 * The navigation already hides these, but a URL can still be typed, bookmarked or
 * shared. Without this the page mounts, fires its requests, collects a fistful
 * of 403s and renders as a broken dashboard — which reads as "ARIA is broken"
 * rather than "this isn't yours". One honest panel is better.
 *
 * This is presentation. The server refuses these routes regardless of what the
 * browser believes, so bypassing this component buys nothing but a blank page.
 */
import React from 'react'
import { useAuth } from './AuthContext'

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
    <div style={{ maxWidth: 560, margin: '8vh auto 0' }}>
      <div className="bb-card" style={{ padding: '26px 24px' }}>
        <span className="aria-pill info">Owner only</span>
        <h1 style={{ fontSize: 22, margin: '14px 0 0' }}>
          {what} requires owner access.
        </h1>
        <p style={{ fontSize: 14.5, color: 'var(--ink-2)', lineHeight: 1.6, margin: '10px 0 0' }}>
          This session is not signed in as the owner. Positions, the trading
          desk, ARIA’s private memory and the track record open only to an
          owner session.
        </p>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 20 }}>
          <button onClick={signIn} className="aria-btn primary">Sign in as owner</button>
        </div>
      </div>
    </div>
  )
}
