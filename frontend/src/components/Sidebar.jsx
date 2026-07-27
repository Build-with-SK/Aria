import React, { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useHealth } from '../hooks/useApi'
import axios from 'axios'

/* ── command rail — grouped, glowing, alive ── */
const GROUPS = [
  {
    label: 'INTELLIGENCE',
    items: [
      { path: '/chat',      label: 'ARIA CHAT',  icon: '◉' },
      { path: '/thinking',  label: 'LIVE MIND',  icon: '✦' },
      { path: '/brain',     label: 'AI BRAIN',   icon: '◈' },
      { path: '/nexus',     label: 'NEXUS',      icon: '◬' },
      { path: '/quantlab',  label: 'QUANT LAB',  icon: '⚗' },
    ],
  },
  {
    label: 'MARKETS',
    items: [
      { path: '/',          label: 'COMMAND',    icon: '⌂' },
      { path: '/explorer',  label: 'EXPLORER',   icon: '⌕' },
      { path: '/signals',   label: 'SIGNALS',    icon: '∿' },
      { path: '/recommendations', label: 'RECOMMEND', icon: '★' },
      { path: '/macro',     label: 'MACRO',      icon: '⊕' },
      { path: '/futures',   label: 'FUTURES',    icon: '◆' },
      { path: '/options',   label: 'OPTIONS',    icon: '◇' },
    ],
  },
  {
    label: 'ANALYTICS',
    items: [
      { path: '/quant',     label: 'QUANT',      icon: 'ƒ' },
      { path: '/ml',        label: 'ML / AI',    icon: '◎' },
      { path: '/portfolio', label: 'PORTFOLIO',  icon: '▣' },
      { path: '/backtest',  label: 'BACKTEST',   icon: '▷' },
      { path: '/compare',   label: 'COMPARE',    icon: '⚡' },
    ],
  },
  {
    label: 'OPERATIONS',
    items: [
      { path: '/desk',      label: 'THE DESK',   icon: '▦' },
      { path: '/execute',   label: 'EXECUTE',    icon: '▶', badge: true },
      { path: '/alerts',    label: 'ALERTS',     icon: '▲' },
      { path: '/report',    label: 'REPORT',     icon: '≡' },
    ],
  },
]

export default function Sidebar() {
  const { data: health } = useHealth()
  const loc = useLocation()
  const [pendingCount, setPendingCount] = useState(0)

  useEffect(() => {
    const poll = () => {
      axios.get('/api/execute/queue?status=pending')
        .then(r => setPendingCount(r.data?.count || 0))
        .catch(() => {})
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => clearInterval(id)
  }, [])

  const live = health?.status === 'ok'

  return (
    <div style={{
      position: 'fixed', left: 0, top: 0, bottom: 0, width: 200,
      background: 'linear-gradient(180deg, rgba(255,36,71,0.03), transparent 30%), #050206',
      borderRight: '1px solid var(--border)',
      display: 'flex', flexDirection: 'column',
      zIndex: 100,
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

      {/* ── nav groups ── */}
      <nav style={{ flex: 1, overflowY: 'auto', padding: '6px 0 10px' }}>
        {GROUPS.map(g => (
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

      {/* ── footer ── */}
      <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)' }}>
        <div style={{ fontFamily: 'var(--mono)', fontSize: 8, color: 'var(--muted)', lineHeight: 1.7, letterSpacing: '0.06em' }}>
          RESEARCH & EDUCATION ONLY<br />NOT FINANCIAL ADVICE<br />
          <span style={{ color: 'var(--orange-dim)' }}>ARIA · by Ariyan</span>
        </div>
      </div>
    </div>
  )
}
