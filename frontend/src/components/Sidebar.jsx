import React, { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useHealth } from '../hooks/useApi'
import axios from 'axios'

const NAV = [
  { path: '/chat',      label: 'ARIA CHAT', icon: '◉' },
  { path: '/nexus',     label: 'NEXUS',     icon: '◬', highlight: true },
  { path: '/quantlab',  label: 'QUANT LAB', icon: '⚗', highlight: true },
  { path: '/brain',     label: 'AI BRAIN',  icon: '◈', highlight: true },
  { path: '/thinking',  label: 'LIVE MIND', icon: '✦', highlight: true },
  { path: '/map',       label: 'SIGNAL MAP', icon: '◉', highlight: true },
  { path: '/explorer',  label: 'EXPLORER',  icon: '⌕', highlight: true },
  { path: '/execute',   label: 'EXECUTE',   icon: '▶', highlight: true },
  { path: '/compare',   label: 'COMPARE',   icon: '⚡' },
  { path: '/',          label: 'OVERVIEW',  icon: '◈' },
  { path: '/signals',   label: 'SIGNALS',   icon: '◉' },
  { path: '/macro',     label: 'MACRO',     icon: '⊕' },
  { path: '/ml',        label: 'ML / AI',   icon: '◎' },
  { path: '/portfolio', label: 'PORTFOLIO', icon: '▣' },
  { path: '/backtest',  label: 'BACKTEST',  icon: '▷' },
  { path: '/quant',     label: 'QUANT',     icon: 'ƒ' },
  { path: '/futures',   label: 'FUTURES',   icon: '◆' },
  { path: '/options',   label: 'OPTIONS',   icon: '◇' },
  { path: '/alerts',    label: 'ALERTS',    icon: '▲' },
  { path: '/report',    label: 'REPORT',    icon: '≡' },
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

  return (
    <div style={{
      position: 'fixed', left: 0, top: 0, bottom: 0, width: 180,
      background: '#050505',
      borderRight: '1px solid var(--border)',
      display: 'flex', flexDirection: 'column',
      zIndex: 100,
    }}>
      {/* Logo */}
      <div style={{ padding: '14px 14px 10px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ fontFamily: 'var(--mono)', fontSize: 13, fontWeight: 700, color: 'var(--orange)', letterSpacing: '0.1em' }}>
          ▶ ARIA
        </div>
        <div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--muted)', letterSpacing: '0.08em', marginTop: 2 }}>
          TRADING INTELLIGENCE
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 8 }}>
          <div style={{
            width: 6, height: 6, borderRadius: '50%',
            background: health?.status === 'ok' ? 'var(--green)' : 'var(--red)',
            boxShadow: health?.status === 'ok' ? '0 0 6px var(--green)' : '0 0 6px var(--red)',
          }} />
          <span style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--muted)' }}>
            {health?.status === 'ok' ? 'LIVE' : 'OFFLINE'}
          </span>
        </div>
      </div>

      {/* Nav */}
      <nav style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
        {NAV.map(({ path, label, icon, highlight }) => {
          const active = loc.pathname === path
          const hasBadge = path === '/execute' && pendingCount > 0
          return (
            <NavLink key={path} to={path} style={{ textDecoration: 'none' }}>
              <div style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '8px 14px',
                background: active ? 'var(--orange-bg)' : hasBadge ? 'rgba(255,204,0,0.04)' : 'transparent',
                borderLeft: active ? '2px solid var(--orange)' : hasBadge ? '2px solid var(--yellow)' : '2px solid transparent',
                cursor: 'pointer',
              }}>
                <span style={{ fontSize: 10, color: active ? 'var(--orange)' : hasBadge ? 'var(--yellow)' : 'var(--muted)' }}>{icon}</span>
                <span style={{
                  fontFamily: 'var(--mono)', fontSize: 10, fontWeight: 700,
                  letterSpacing: '0.1em',
                  color: active ? 'var(--orange)' : hasBadge ? 'var(--yellow)' : 'var(--muted)',
                  flex: 1,
                }}>{label}</span>
                {hasBadge && (
                  <span style={{
                    background: 'var(--yellow)', color: '#000',
                    borderRadius: 10, fontSize: 9, fontWeight: 800,
                    padding: '1px 6px', fontFamily: 'var(--mono)',
                  }}>{pendingCount}</span>
                )}
              </div>
            </NavLink>
          )
        })}
      </nav>

      <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border)' }}>
        <div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: '#333', lineHeight: 1.6 }}>
          ⚠ RESEARCH ONLY<br />NOT FINANCIAL ADVICE
        </div>
      </div>
    </div>
  )
}
