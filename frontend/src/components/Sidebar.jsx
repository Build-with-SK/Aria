import React, { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useHealth } from '../hooks/useApi'
import { CURRENCY_META, useCurrency } from '../currency/CurrencyContext'
import Settings from './Settings'
import { useAuth } from '../auth/AuthContext'
import axios from 'axios'

/* ── command rail — twelve destinations, grouped by the question they answer ──
   Was twenty-two. Four of those pages answered "should I buy this?" and five
   were "the AI thinking"; the merged ones are now tabs inside the page they
   belong to, and every old path still redirects.                            */
/* `owner: true` means the destination is not merely refused for other people —
   it is not shown to them at all. A rail full of doors that answer 403 is a
   worse experience than a shorter rail, and it also stops advertising what
   exists on the other side. The server refuses these regardless; hiding them
   is courtesy, never the control. */
export const GROUPS = [
  {
    label: 'INTELLIGENCE',
    items: [
      { path: '/chat',      label: 'ARIA CHAT',  icon: '◉' },
      { path: '/v5',        label: 'ARIA V5',    icon: '◆' },
      { path: '/research',  label: 'RESEARCH',   icon: '◬' },
      { path: '/lab',       label: 'QUANT LAB',  icon: '⚗' },
      { path: '/brain',     label: 'BRAIN',      icon: '◈', owner: true },
    ],
  },
  {
    label: 'MARKETS',
    items: [
      { path: '/',          label: 'COMMAND',    icon: '⌂' },
      { path: '/markets',   label: 'MARKETS',    icon: '∿' },
      { path: '/recommendations', label: 'RECOMMEND', icon: '★' },
    ],
  },
  {
    label: 'PORTFOLIO',
    items: [
      { path: '/portfolio', label: 'PORTFOLIO',  icon: '▣', owner: true },
      { path: '/stress',    label: 'STRESS',     icon: 'ƒ' },
    ],
  },
  {
    label: 'LEARNING',
    items: [
      { path: '/track-record', label: 'TRACK RECORD', icon: '◎', owner: true },
    ],
  },
  {
    label: 'OPERATIONS',
    items: [
      { path: '/desk',      label: 'THE DESK',   icon: '▦', badge: true, owner: true },
    ],
  },
]

/** The rail as a given role should see it — groups that empty out disappear. */
export function groupsFor(isOwner) {
  if (isOwner) return GROUPS
  return GROUPS
    .map(g => ({ ...g, items: g.items.filter(i => !i.owner) }))
    .filter(g => g.items.length > 0)
}

export default function Sidebar() {
  const { data: health } = useHealth()
  const { display } = useCurrency()
  const { owner, user } = useAuth()
  const loc = useLocation()
  const [pendingCount, setPendingCount] = useState(0)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [railOpen, setRailOpen] = useState(false)
  // Close the off-canvas rail after a navigation on small screens.
  useEffect(() => { setRailOpen(false) }, [loc.pathname])

  const groups = groupsFor(owner)

  useEffect(() => {
    // The approval queue is owner-only, so polling it as anyone else was a 403
    // every five seconds forever — noise in the audit log that would bury a
    // real intrusion, and a request that could never succeed.
    if (!owner) { setPendingCount(0); return }
    const poll = () => {
      axios.get('/api/execute/queue?status=pending')
        .then(r => setPendingCount(r.data?.count || 0))
        .catch(() => {})
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => clearInterval(id)
  }, [owner])

  const live = health?.status === 'ok'

  return (
    <>
    {/* Narrow screens: a button to reveal the rail, which is off-canvas there. */}
    <button className="aria-rail-toggle" onClick={() => setRailOpen(o => !o)}
      aria-label={railOpen ? 'Hide navigation' : 'Show navigation'} aria-expanded={railOpen}>
      {railOpen ? '✕' : '☰'}
    </button>
    {railOpen && <div className="aria-rail-scrim" onClick={() => setRailOpen(false)} />}
    {/* The open offset is set inline rather than by a `.is-open` CSS rule:
        the class applied correctly but the media-query declaration kept
        winning the cascade, so the rail never actually slid in. An inline
        style is unambiguous and cannot be out-specified. */}
    <div className={`aria-rail${railOpen ? ' is-open' : ''}`} style={{
      background: 'linear-gradient(180deg, rgba(255,36,71,0.03), transparent 30%), #050206',
      borderRight: '1px solid var(--border)',
      ...(railOpen ? { left: 0 } : null),
    }}>
      {/* ── wordmark + living core ── */}
      <div style={{ padding: '16px 16px 12px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {/* the core — concentric pulse */}
          <div style={{ position: 'relative', width: 26, height: 26, flexShrink: 0 }}>
            <div style={{
              position: 'absolute', inset: 0, borderRadius: '50%',
              border: '1px solid rgba(255,36,71,.5)',
              animation: 'corePulse 2.6s ease-in-out infinite',
            }} />
            <div style={{
              position: 'absolute', inset: 5, borderRadius: '50%',
              border: '1px solid rgba(255,36,71,.35)',
              animation: 'corePulse 2.6s .4s ease-in-out infinite',
            }} />
            <div style={{
              position: 'absolute', inset: 10, borderRadius: '50%',
              background: live ? 'var(--orange)' : 'var(--muted)',
              boxShadow: live ? 'var(--glow)' : 'none',
              animation: live ? 'corePulse 2.6s .8s ease-in-out infinite' : 'none',
            }} />
          </div>
          <div>
            <div style={{
              fontFamily: 'var(--mono)', fontSize: 17, fontWeight: 800,
              color: 'var(--orange)', letterSpacing: '0.22em',
              textShadow: '0 0 14px rgba(255,36,71,.6)',
              animation: 'flicker 6s infinite',
            }}>
              ARIA
            </div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--muted)', letterSpacing: '0.18em', marginTop: 1 }}>
              OPEN FINANCE INTEL
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 10 }}>
          <div style={{
            width: 6, height: 6, borderRadius: '50%',
            background: live ? 'var(--green)' : 'var(--red)',
            boxShadow: live ? '0 0 8px var(--green)' : '0 0 8px var(--red)',
            animation: 'corePulse 2s ease-in-out infinite',
          }} />
          <span style={{ fontFamily: 'var(--mono)', fontSize: 9, letterSpacing: '0.15em', color: live ? 'var(--green)' : 'var(--red)' }}>
            {live ? 'SYSTEMS LIVE' : 'OFFLINE'}
          </span>
        </div>
      </div>

      {/* ── search / command palette ──
          A shortcut nobody knows about is worth very little, so the rail
          advertises it and doubles as the button for anyone who would rather
          click. Both routes end in the same palette. */}
      <button
        onClick={() => window.dispatchEvent(new CustomEvent('aria:palette'))}
        aria-label="Open command palette"
        style={{
          fontFamily: 'var(--mono)', display: 'flex', alignItems: 'center', gap: 8,
          margin: '10px 12px 2px', padding: '7px 9px', width: 'calc(100% - 24px)',
          background: '#0c0a0c', border: '1px solid var(--border)', borderRadius: 3,
          color: 'var(--muted)', fontSize: 10, letterSpacing: '0.08em', cursor: 'pointer',
        }}>
        <span aria-hidden="true" style={{ color: 'var(--orange)' }}>⌕</span>
        <span style={{ flex: 1, textAlign: 'left' }}>SEARCH / TICKER</span>
        <kbd style={{ fontFamily: 'var(--mono)', fontSize: 8, border: '1px solid var(--border)', borderRadius: 2, padding: '1px 4px' }}>
          ⌘K
        </kbd>
      </button>

      {/* ── nav groups ── */}
      <nav style={{ flex: 1, overflowY: 'auto', padding: '6px 0 10px' }}>
        {groups.map(g => (
          <div key={g.label}>
            <div style={{
              fontFamily: 'var(--mono)', fontSize: 8, fontWeight: 800,
              letterSpacing: '0.28em', color: 'var(--muted)',
              padding: '12px 16px 5px',
            }}>{g.label}</div>
            {g.items.map(({ path, label, icon, badge }) => {
              const active = loc.pathname === path
              const hasBadge = badge && pendingCount > 0
              return (
                <NavLink key={path} to={path} style={{ textDecoration: 'none' }}>
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 9,
                    padding: '6px 16px', position: 'relative',
                    background: active ? 'linear-gradient(90deg, rgba(255,36,71,.12), transparent)' : 'transparent',
                    borderLeft: active ? '2px solid var(--orange)' : '2px solid transparent',
                    cursor: 'pointer', transition: 'background .2s',
                  }}
                    onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'rgba(255,36,71,.05)' }}
                    onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent' }}
                  >
                    <span style={{
                      fontSize: 10, width: 12, textAlign: 'center',
                      color: active ? 'var(--orange)' : hasBadge ? 'var(--yellow)' : 'var(--muted)',
                      textShadow: active ? '0 0 8px rgba(255,36,71,.7)' : 'none',
                    }}>{icon}</span>
                    <span style={{
                      fontFamily: 'var(--mono)', fontSize: 10, fontWeight: 700,
                      letterSpacing: '0.12em', flex: 1,
                      color: active ? '#fff' : hasBadge ? 'var(--yellow)' : 'var(--text-dim)',
                      textShadow: active ? '0 0 10px rgba(255,36,71,.5)' : 'none',
                    }}>{label}</span>
                    {hasBadge && (
                      <span style={{
                        background: 'var(--yellow)', color: '#000',
                        borderRadius: 8, fontSize: 8, fontWeight: 800,
                        padding: '1px 6px', fontFamily: 'var(--mono)',
                        boxShadow: '0 0 10px rgba(255,179,36,.5)',
                      }}>{pendingCount}</span>
                    )}
                  </div>
                </NavLink>
              )
            })}
          </div>
        ))}
      </nav>

      {/* ── settings ── */}
      <div style={{ padding: '8px 16px', borderTop: '1px solid var(--border)' }}>
        <button onClick={() => setSettingsOpen(true)} aria-label="Open settings"
          style={{
            display: 'flex', alignItems: 'center', gap: 9, width: '100%',
            background: 'none', border: '1px solid var(--border)', borderRadius: 4,
            padding: '7px 9px', cursor: 'pointer', fontFamily: 'var(--mono)', minHeight: 30,
          }}>
          <span aria-hidden="true" style={{ fontSize: 12, color: 'var(--orange)' }}>⚙</span>
          <span style={{
            fontSize: 10, fontWeight: 700, letterSpacing: '.12em',
            color: 'var(--text-dim)', flex: 1, textAlign: 'left',
          }}>SETTINGS</span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--orange)' }}>
            {CURRENCY_META[display]?.symbol}
          </span>
        </button>
      </div>
      <Settings open={settingsOpen} onClose={() => setSettingsOpen(false)} />

      {/* ── footer ── */}
      <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)' }}>
        <div style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--muted)', lineHeight: 1.7, letterSpacing: '0.06em' }}>
          RESEARCH & EDUCATION ONLY<br />NOT FINANCIAL ADVICE<br />
          <span style={{ color: 'var(--orange-dim)' }}>ARIA · by Ariyan</span>
        </div>
      </div>
    </div>
    </>
  )
}
