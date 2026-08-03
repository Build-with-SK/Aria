/**
 * components/Settings.jsx
 * One place for everything that changes how ARIA looks and behaves.
 *
 * The currency picker and the accessibility controls used to sit loose in the
 * sidebar, competing with navigation for attention. Settings are not
 * navigation — you set them once and forget them — so they live behind a
 * single button and open in a dialog that keeps your place on the page.
 *
 * Accessible dialog: focus moves in on open, Escape closes, the backdrop
 * closes, and focus returns to the button that opened it.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { CURRENCY_META, useCurrency } from '../currency/CurrencyContext'
import { applyA11y, loadA11y } from './Accessibility'

const MONO = 'var(--mono)'
const A11Y_KEY = 'aria-a11y'

const SIZES = [
  { id: 'normal', label: 'Normal' },
  { id: 'large', label: 'Large' },
  { id: 'larger', label: 'Larger' },
  { id: 'largest', label: 'Largest' },
]

function Section({ title, hint, children }) {
  return (
    <div style={{ marginBottom: 22 }}>
      <div style={{ fontFamily: MONO, fontSize: 10, fontWeight: 800, letterSpacing: '.18em', color: 'var(--orange)' }}>
        {title}
      </div>
      {hint && (
        <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--muted)', marginTop: 3, lineHeight: 1.6 }}>
          {hint}
        </div>
      )}
      <div style={{ marginTop: 9 }}>{children}</div>
    </div>
  )
}

function Choice({ options, value, onChange, ariaLabel }) {
  return (
    <div role="group" aria-label={ariaLabel} style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      {options.map(o => {
        const active = value === o.id
        return (
          <button key={o.id} onClick={() => onChange(o.id)} aria-pressed={active}
            style={{
              fontFamily: MONO, fontSize: 11, fontWeight: 700, padding: '7px 14px',
              cursor: 'pointer', borderRadius: 4, minHeight: 30,
              background: active ? 'var(--orange)' : 'transparent',
              color: active ? '#000' : 'var(--text-dim)',
              border: `1px solid ${active ? 'var(--orange)' : 'var(--border-2)'}`,
            }}>
            {o.label}
          </button>
        )
      })}
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
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,.74)', zIndex: 300,
        display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '6vh 20px',
      }}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        onClick={e => e.stopPropagation()}
        style={{
          background: '#0b0609', border: '1px solid var(--border-2)', borderRadius: 8,
          width: '100%', maxWidth: 560, maxHeight: '84vh', overflowY: 'auto', padding: 22,
        }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 18 }}>
          <div>
            <div style={{ fontFamily: MONO, fontSize: 15, fontWeight: 800, color: 'var(--text)', letterSpacing: '.1em' }}>
              SETTINGS
            </div>
            <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>
              Saved on this device only — nothing leaves your machine
            </div>
          </div>
          <button ref={firstRef} onClick={onClose} aria-label="Close settings"
            style={{
              fontFamily: MONO, fontSize: 11, padding: '6px 14px', cursor: 'pointer', minHeight: 30,
              background: 'none', border: '1px solid var(--border-2)', color: 'var(--muted)', borderRadius: 4,
            }}>CLOSE</button>
        </div>

        <Section
          title="DISPLAY CURRENCY"
          hint={`Every price is converted into this currency. ${detected.source}. Conversion is for
                 display only — daily rates, no spread${meta?.as_of ? `, as of ${String(meta.as_of).slice(0, 16)}` : ''}.`}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(132px, 1fr))', gap: 6 }}>
            {currencies.map(c => {
              const active = c === display
              return (
                <button key={c} onClick={() => setDisplay(c)} aria-pressed={active}
                  style={{
                    display: 'flex', alignItems: 'baseline', gap: 7, padding: '7px 10px',
                    cursor: 'pointer', borderRadius: 4, minHeight: 32, textAlign: 'left',
                    background: active ? 'var(--orange-bg)' : 'transparent',
                    border: `1px solid ${active ? 'var(--orange)' : 'var(--border)'}`,
                  }}>
                  <span style={{ fontFamily: MONO, fontSize: 13, color: 'var(--orange)', fontWeight: 800, minWidth: 22 }}>
                    {CURRENCY_META[c].symbol}
                  </span>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--text)', fontWeight: 700 }}>{c}</span>
                  <span style={{ fontSize: 10, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {CURRENCY_META[c].name}
                  </span>
                </button>
              )
            })}
          </div>
        </Section>

        <Section
          title="TEXT SIZE"
          hint="Scales the whole interface. The default terminal type is small — if you are reading
                this at arm's length, try Large or Larger.">
          <Choice ariaLabel="Text size" options={SIZES} value={a11y.textSize}
            onChange={v => set({ textSize: v })} />
        </Section>

        <Section
          title="CONTRAST"
          hint="High contrast brightens text and borders to clear the WCAG AA readability
                threshold. The default grey sits below it.">
          <Choice ariaLabel="Contrast" value={a11y.contrast} onChange={v => set({ contrast: v })}
            options={[{ id: 'normal', label: 'Normal' }, { id: 'high', label: 'High contrast' }]} />
        </Section>

        <Section
          title="MOTION"
          hint="Stops the scrolling ticker, pulsing indicators and transitions. Nothing in ARIA
                needs motion to be understood.">
          <Choice ariaLabel="Motion" value={a11y.motion} onChange={v => set({ motion: v })}
            options={[{ id: 'normal', label: 'Animations on' }, { id: 'reduced', label: 'Reduce motion' }]} />
        </Section>

        <Section title="RESET">
          <button onClick={resetAll}
            style={{
              fontFamily: MONO, fontSize: 11, fontWeight: 700, padding: '7px 14px', minHeight: 30,
              cursor: 'pointer', background: 'none', border: '1px solid var(--border-2)',
              color: 'var(--text-dim)', borderRadius: 4,
            }}>
            RESET TO DETECTED DEFAULTS
          </button>
        </Section>

        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <div style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--muted)', lineHeight: 1.7 }}>
            ARIA · research and education only · not financial advice · not a licensed adviser.
            Market data from Yahoo Finance, delayed on many venues. Nothing in this app places an
            order without your explicit approval.
          </div>
        </div>
      </div>
    </div>
  )
}
