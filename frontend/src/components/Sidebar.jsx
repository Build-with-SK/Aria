import React, { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useHealth, useSystemHealth } from '../hooks/useApi'
import { CURRENCY_META, useCurrency } from '../currency/CurrencyContext'
import Settings from './Settings'
import { useAuth } from '../auth/AuthContext'
import axios from 'axios'

/* ── the command rail — EIGHT workspaces ──
   Was twelve, and twenty-two before that. The grouping is gone with them: at
   this size a group header costs more attention than it saves.

   The rule (§25) is that the rail names workspaces, not implementation. There
   is no SIGNALS entry because signals are something MARKET knows; no APPROVAL
   QUEUE because approving is a step PORTFOLIO contains; and no LIVE MIND,
   because watching ARIA think is ARIA — that is now literally true rather
   than a stated intention: BRAIN is the one destination, and chat, memory, the
   reasoning stream and vault knowledge are sections inside it.

   DAILY REPORT is a destination and MARKET is a destination, and they are
   deliberately not the same one: the report is the slow daily product, the
   live world feed is continuous, and burying the first inside the second was
   what made both hard to read.                                              */
/* `owner: true` means the destination is not merely refused for other people —
   it is not shown to them at all. A rail full of doors that answer 403 is a
   worse experience than a shorter rail, and it also stops advertising what
   exists on the other side. The server refuses these regardless; hiding them
   is courtesy, never the control. */
export const GROUPS = [
  {
    label: '',
    items: [
      { path: '/brain',        label: 'BRAIN',        icon: '◉' },
      { path: '/research',     label: 'RESEARCH',     icon: '◬' },
      { path: '/market',       label: 'MARKET',       icon: '∿' },
      { path: '/portfolio',    label: 'PORTFOLIO',    icon: '▣', badge: true, owner: true },
      { path: '/strategies',   label: 'STRATEGIES',   icon: '⚗' },
      { path: '/daily-report', label: 'DAILY REPORT', icon: '▤', owner: true },
      { path: '/track-record', label: 'TRACK RECORD', icon: '◎', owner: true },
      { path: '/system',       label: 'SYSTEM',       icon: '⚙' },
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
  const { data: sysHealth } = useSystemHealth()
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

  // `/health` answers ok whenever the web server can answer, which says nothing
  // about whether the daemons behind it are running — this system already went
  // a week with a dead prediction loop and a green dot. The rail now reads the
  // worker registry, so it can say DEGRADED.
  const apiUp = health?.status === 'ok'
  const workersOk = sysHealth?.workers?.healthy
  const live = apiUp && workersOk !== false
  const degraded = apiUp && workersOk === false
  const railState = !apiUp ? { c: 'var(--red)', t: 'OFFLINE' }
    : degraded ? { c: 'var(--yellow)', t: 'DEGRADED' }
    : { c: 'var(--green)', t: 'SYSTEMS LIVE' }

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
            background: railState.c,
            boxShadow: `0 0 8px ${railState.c}`,
            animation: live ? 'corePulse 2s ease-in-out infinite' : 'none',
          }} />
          <NavLink to="/system" style={{ textDecoration: 'none' }}
            title={degraded ? (sysHealth?.workers?.degraded || []).join(', ') : undefined}>
            <span style={{ fontFamily: 'var(--mono)', fontSize: 9, letterSpacing: '0.15em', color: railState.c }}>
              {railState.t}
            </span>
          </NavLink>
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
            {g.label && (
              <div style={{
                fontFamily: 'var(--mono)', fontSize: 8, fontWeight: 800,
                letterSpacing: '0.28em', color: 'var(--muted)',
                padding: '12px 16px 5px',
              }}>{g.label}</div>
            )}
            {g.items.map(({ path, label, icon, badge }) => {
              // `/` and `/brain` are one destination, so the rail must not
              // go dark on the landing page.
              const active = loc.pathname === path
                || (path === '/brain' && loc.pathname === '/')
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
          <span style={{ color: 'var(--orange-dim)' }}>ARIA · by Soundariyan Karunakaran</span>
        </div>
      </div>
    </div>
    </>
  )
}
