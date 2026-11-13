/**
 * components/Settings.jsx
 * One place for everything that changes how ARIA looks and behaves.
 *
 * Settings are not navigation — you set them once and forget them — so they
 * live behind a single button and open in a sheet that keeps your place on
 * the page.
 *
 * Accessible dialog: focus moves in on open, Escape closes, the backdrop
 * closes, and focus returns to the button that opened it.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { CURRENCY_META, useCurrency } from '../currency/CurrencyContext'
import { applyA11y, loadA11y } from './Accessibility'
import { useAuth } from '../auth/AuthContext'

const A11Y_KEY = 'aria-a11y'

const SIZES = [
  { id: 'normal', label: 'Normal' },
  { id: 'large', label: 'Large' },
  { id: 'larger', label: 'Larger' },
  { id: 'largest', label: 'Largest' },
]
const THEMES = [
  { id: 'system', label: 'Match device' },
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
]

function Section({ title, hint, children }) {
  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ fontFamily: 'var(--font-display)', fontSize: 15, fontWeight: 650, color: 'var(--ink)' }}>
        {title}
      </div>
      {hint && (
        <div style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 3, lineHeight: 1.55 }}>
          {hint}
        </div>
      )}
      <div style={{ marginTop: 10 }}>{children}</div>
    </div>
  )
}

/* A segmented control: one choice of a few. */
function Choice({ options, value, onChange, ariaLabel }) {
  return (
    <div role="group" aria-label={ariaLabel} style={{
      display: 'inline-flex', gap: 4, flexWrap: 'wrap', padding: 4, borderRadius: 999, background: 'var(--inset)',
    }}>
      {options.map(o => {
        const active = value === o.id
        return (
          <button key={o.id} onClick={() => onChange(o.id)} aria-pressed={active}
            style={{
              fontSize: 13.5, fontWeight: active ? 600 : 550, padding: '0 15px', minHeight: 34,
              borderRadius: 999, border: 'none',
              background: active ? 'var(--sheet)' : 'transparent',
              color: active ? 'var(--ink)' : 'var(--ink-2)',
              boxShadow: active ? '0 1px 4px rgba(10,20,50,.14)' : 'none',
            }}>
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

/**
 * The account block.
 *
 * ARIA has one account — the owner's — and no sign-up. Offering registration
 * on a single-owner system is a door to nowhere.
 */
function Account() {
  const { ready, authenticated, owner, role, user, logout } = useAuth()
  const [busy, setBusy] = useState(false)

  const signOut = async () => {
    setBusy(true)
    // logout() posts to /api/auth/logout, which REVOKES the session record
    // server-side and then clears the cookie. There is nothing to clear in the
    // browser beyond that — it no longer holds a credential of its own.
    try { await logout() } finally { window.location.href = '/app/login' }
  }

  const go = () => { window.location.href = '/app/login' }
  const label = !ready ? 'checking…'
    : owner ? 'Owner'
    : authenticated ? 'Signed in'
    : 'Not signed in'

  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      gap: 12, padding: '12px 14px', borderRadius: 16, background: 'var(--inset)',
    }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--ink)' }}>
          {user?.email || user?.name || label}
        </div>
        <div style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 1 }}>
          {label}{role ? ` · ${role}` : ''}
        </div>
      </div>
      {authenticated ? (
        <button onClick={signOut} disabled={busy} className="aria-btn">{busy ? 'Signing out…' : 'Sign out'}</button>
      ) : (
        <button onClick={go} className="aria-btn primary">Sign in</button>
      )}
    </div>
  )
}

export default function Settings({ open, onClose }) {
  const { display, setDisplay, detected, rates, meta } = useCurrency()
  const [a11y, setA11y] = useState(loadA11y)
  const dialogRef = useRef(null)
  const firstRef = useRef(null)

  useEffect(() => {
    applyA11y(a11y)
    try { localStorage.setItem(A11Y_KEY, JSON.stringify(a11y)) } catch { /* storage blocked */ }
  }, [a11y])

  useEffect(() => {
    if (!open) return
    const opener = document.activeElement
    firstRef.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (opener && opener.focus) opener.focus()
    }
  }, [open, onClose])

  const set = useCallback((patch) => setA11y(p => ({ ...p, ...patch })), [])

  const resetAll = () => {
    try {
      localStorage.removeItem(A11Y_KEY)
      localStorage.removeItem('aria-display-currency')
    } catch { /* ignore */ }
    const fresh = loadA11y()
    setA11y(fresh)
    applyA11y(fresh)
    setDisplay(detected.code)
  }

  if (!open) return null

  const currencies = Object.keys(CURRENCY_META).filter(c => c === 'USD' || rates?.[c])

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'var(--scrim)', zIndex: 320,
        display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '6vh 16px',
        animation: 'fadeIn .18s ease both',
      }}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        className="sheet"
        onClick={e => e.stopPropagation()}
        style={{ width: '100%', maxWidth: 580, maxHeight: '86vh', overflowY: 'auto', padding: '22px 24px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 22 }}>
          <div>
            <h2 style={{ fontSize: 24, fontWeight: 700 }}>Settings</h2>
            <div style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 2 }}>
              Display preferences are saved on this device only.
            </div>
          </div>
          <button ref={firstRef} onClick={onClose} aria-label="Close settings" className="aria-btn">Done</button>
        </div>

        <Section
          title="Account"
          hint="Signing out ends this session on the server immediately. This browser
                never stores the owner token.">
          <Account />
        </Section>

        <Section title="Appearance" hint="Light, dark, or whatever this device is set to.">
          <Choice ariaLabel="Appearance" options={THEMES} value={a11y.theme || 'system'}
            onChange={v => set({ theme: v })} />
        </Section>

        <Section
          title="Display currency"
          hint={`Every price is converted into this currency. ${detected.source}. Conversion is for
                 display only — daily rates, no spread${meta?.as_of ? `, as of ${String(meta.as_of).slice(0, 16)}` : ''}.`}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 8 }}>
            {currencies.map(c => {
              const active = c === display
              return (
                <button key={c} onClick={() => setDisplay(c)} aria-pressed={active}
                  style={{
                    display: 'flex', alignItems: 'baseline', gap: 8, padding: '9px 12px',
                    borderRadius: 12, minHeight: 40, textAlign: 'left',
                    background: active ? 'var(--accent-bg)' : 'var(--inset)',
                    border: `1px solid ${active ? 'var(--accent)' : 'transparent'}`,
                  }}>
                  <span style={{ fontSize: 14, color: 'var(--accent)', fontWeight: 650, minWidth: 22 }}>
                    {CURRENCY_META[c].symbol}
                  </span>
                  <span style={{ fontSize: 14, color: 'var(--ink)', fontWeight: 600 }}>{c}</span>
                  <span style={{ fontSize: 12.5, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {CURRENCY_META[c].name}
                  </span>
                </button>
              )
            })}
          </div>
        </Section>

        <Section
          title="Text size"
          hint="Scales the whole interface. If you are reading this at arm's length,
                try Large or Larger.">
          <Choice ariaLabel="Text size" options={SIZES} value={a11y.textSize}
            onChange={v => set({ textSize: v })} />
        </Section>

        <Section
          title="Contrast"
          hint="High contrast darkens secondary text and outlines and makes panels solid.">
          <Choice ariaLabel="Contrast" value={a11y.contrast} onChange={v => set({ contrast: v })}
            options={[{ id: 'normal', label: 'Normal' }, { id: 'high', label: 'High contrast' }]} />
        </Section>

        <Section
          title="Motion"
          hint="Stops the scrolling signal strip, pulsing indicators and transitions. Nothing in ARIA
                needs motion to be understood.">
          <Choice ariaLabel="Motion" value={a11y.motion} onChange={v => set({ motion: v })}
            options={[{ id: 'normal', label: 'Animations on' }, { id: 'reduced', label: 'Reduce motion' }]} />
        </Section>

        <Section title="Reset">
          <button onClick={resetAll} className="aria-btn">Reset to detected defaults</button>
        </Section>

        <div style={{ borderTop: '1px solid var(--line)', paddingTop: 14 }}>
          <div style={{ fontSize: 12.5, color: 'var(--ink-3)', lineHeight: 1.65 }}>
            ARIA · research and education only · not financial advice · not a licensed adviser.
            Market data from Yahoo Finance, delayed on many venues. Nothing in this app places an
            order without your explicit approval.
          </div>
        </div>
      </div>
    </div>
  )
}
