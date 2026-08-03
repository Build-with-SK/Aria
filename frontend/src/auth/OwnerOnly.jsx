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
import { Link } from 'react-router-dom'
import { useAuth } from './AuthContext'

const mono = { fontFamily: 'var(--mono)' }

export default function OwnerOnly({ children, what = 'This section' }) {
  const { owner, authenticated, user } = useAuth()
  if (owner) return children

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
          {what} isn’t part of your access.
        </h1>
        <p style={{ ...mono, fontSize: 11.5, color: 'var(--muted)', lineHeight: 1.7, margin: '12px 0 0' }}>
          Positions, the trading desk, ARIA’s private memory and the owner’s
          own track record stay with the account that owns them. That isn’t a
          plan you can upgrade to — it’s someone’s money and someone’s notes.
        </p>
        <p style={{ ...mono, fontSize: 11.5, color: 'var(--muted)', lineHeight: 1.7, margin: '12px 0 0' }}>
          Everything ARIA knows about <strong style={{ color: '#c9c2d0' }}>the market</strong> is
          open to you: the 41-module research engine, live quotes across four
          regions, signals, macro, and the quant lab.
        </p>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 18 }}>
          <Link to="/v5" style={btn(true)}>◆ RESEARCH A TICKER</Link>
          <Link to="/markets" style={btn(false)}>∿ MARKETS</Link>
        </div>

        {authenticated && (
          <div style={{ ...mono, fontSize: 10, color: 'var(--muted)', marginTop: 18,
                        borderTop: '1px solid var(--border)', paddingTop: 12 }}>
            Signed in as {user?.email || 'your account'}.
            {' '}If this should be your terminal, set <code>ARIA_OWNER_EMAIL</code> to
            that address and restart.
          </div>
        )}
      </div>
    </div>
  )
}

const btn = primary => ({
  ...mono, fontSize: 11, fontWeight: 700, letterSpacing: '0.06em',
  padding: '9px 14px', borderRadius: 3, textDecoration: 'none',
  background: primary ? 'var(--orange)' : 'transparent',
  color: primary ? '#000' : 'var(--text-dim)',
  border: `1px solid ${primary ? 'var(--orange)' : 'var(--border)'}`,
})
