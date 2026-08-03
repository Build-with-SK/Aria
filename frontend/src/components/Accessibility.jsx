/**
 * components/Accessibility.jsx
 * Make ARIA readable for everyone.
 *
 * The terminal look defaults to 8-11px text on low-contrast greys. That is a
 * deliberate aesthetic, but it excludes people — anyone over about forty
 * reading 9px `--muted` on near-black is working far harder than they should,
 * and the default grey sits below the WCAG AA contrast minimum for body text.
 *
 * Rather than water the design down for everyone, this exposes three controls
 * and remembers the choice:
 *
 *   Text size    scales the entire interface (zoom, so inline px scales too)
 *   Contrast     lifts text and border tokens above WCAG AA
 *   Motion       stops the tape, pulses and transitions
 *
 * Defaults follow the operating system where it says something — a viewer who
 * has already asked their OS for reduced motion or more contrast should not
 * have to ask again here.
 */
import React, { useCallback, useEffect, useState } from 'react'

const KEY = 'aria-a11y'

const SIZES = [
  { id: 'normal', label: 'Normal', sample: 11 },
  { id: 'large', label: 'Large', sample: 13 },
  { id: 'larger', label: 'Larger', sample: 15 },
  { id: 'largest', label: 'Largest', sample: 17 },
]

function osDefaults() {
  const q = (m) => { try { return window.matchMedia(m).matches } catch { return false } }
  return {
    textSize: 'normal',
    contrast: q('(prefers-contrast: more)') ? 'high' : 'normal',
    motion: q('(prefers-reduced-motion: reduce)') ? 'reduced' : 'normal',
  }
}

export function loadA11y() {
  const base = osDefaults()
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || '{}')
    return { ...base, ...saved }
  } catch {
    return base
  }
}

export function applyA11y(s) {
  const el = document.documentElement
  el.setAttribute('data-text-size', s.textSize || 'normal')
  el.setAttribute('data-contrast', s.contrast || 'normal')
  el.setAttribute('data-motion', s.motion || 'normal')
}

/** Applies saved settings on first paint, before anything renders. */
export function useA11yBoot() {
  useEffect(() => { applyA11y(loadA11y()) }, [])
}

export default function AccessibilityControls() {
  const [s, setS] = useState(loadA11y)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    applyA11y(s)
    try { localStorage.setItem(KEY, JSON.stringify(s)) } catch { /* storage blocked */ }
  }, [s])

  const set = useCallback((patch) => setS(prev => ({ ...prev, ...patch })), [])

  const row = { display: 'flex', gap: 4, marginTop: 4 }
  const chip = (active) => ({
    flex: 1, fontFamily: 'var(--mono)', fontSize: 9, fontWeight: 700,
    padding: '4px 0', cursor: 'pointer', borderRadius: 3,
    background: active ? 'var(--orange)' : 'transparent',
    color: active ? '#000' : 'var(--text-dim)',
    border: `1px solid ${active ? 'var(--orange)' : 'var(--border)'}`,
  })

  return (
    <div style={{ padding: '8px 16px', borderTop: '1px solid var(--border)' }}>
      <button
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-label="Display and accessibility settings"
        style={{
          display: 'flex', alignItems: 'center', gap: 7, width: '100%',
          background: 'none', border: 'none', cursor: 'pointer', padding: 0,
          fontFamily: 'var(--mono)',
        }}>
        <span aria-hidden="true" style={{ fontSize: 12, color: 'var(--orange)' }}>◐</span>
        <span style={{
          fontSize: 8, fontWeight: 800, letterSpacing: '.2em',
          color: 'var(--muted)', flex: 1, textAlign: 'left',
        }}>DISPLAY &amp; ACCESS</span>
        <span aria-hidden="true" style={{ fontSize: 8, color: 'var(--muted)' }}>{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div style={{ marginTop: 8 }}>
          <div>
            <label style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--muted)', letterSpacing: '.14em' }}>
              TEXT SIZE
            </label>
            <div style={row}>
              {SIZES.map(z => (
                <button key={z.id} onClick={() => set({ textSize: z.id })}
                  aria-pressed={s.textSize === z.id}
                  title={`${z.label} text`}
                  style={{ ...chip(s.textSize === z.id), fontSize: Math.min(z.sample, 12) }}>
                  A
                </button>
              ))}
            </div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 7.5, color: 'var(--muted)', marginTop: 3 }}>
              {SIZES.find(z => z.id === s.textSize)?.label} — scales the whole interface
            </div>
          </div>

          <div style={{ marginTop: 9 }}>
            <label style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--muted)', letterSpacing: '.14em' }}>
              CONTRAST
            </label>
            <div style={row}>
              <button onClick={() => set({ contrast: 'normal' })} aria-pressed={s.contrast === 'normal'}
                style={chip(s.contrast === 'normal')}>NORMAL</button>
              <button onClick={() => set({ contrast: 'high' })} aria-pressed={s.contrast === 'high'}
                style={chip(s.contrast === 'high')}>HIGH</button>
            </div>
          </div>

          <div style={{ marginTop: 9 }}>
            <label style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--muted)', letterSpacing: '.14em' }}>
              MOTION
            </label>
            <div style={row}>
              <button onClick={() => set({ motion: 'normal' })} aria-pressed={s.motion === 'normal'}
                style={chip(s.motion === 'normal')}>ON</button>
              <button onClick={() => set({ motion: 'reduced' })} aria-pressed={s.motion === 'reduced'}
                style={chip(s.motion === 'reduced')}>REDUCED</button>
            </div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 7.5, color: 'var(--muted)', marginTop: 3 }}>
              Stops the scrolling tape and pulsing indicators
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
