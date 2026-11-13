/**
 * UI.jsx — the small shared pieces every page is built from.
 * Colours, radii and type come from the tokens in index.css.
 */
import React from 'react'

export function Spinner() {
  const dot = delay => ({
    width: 7, height: 7, background: 'var(--accent)', borderRadius: '50%',
    animation: `blink 1.2s ${delay}s ease-in-out infinite`,
  })
  return (
    <div role="status" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 40, gap: 6, color: 'var(--ink-3)', fontSize: 14 }}>
      <div style={dot(0)} /><div style={dot(0.2)} /><div style={dot(0.4)} />
      <span style={{ marginLeft: 8 }}>Loading data</span>
    </div>
  )
}

export function ErrorBox({ message }) {
  return (
    <div className="bb-card" role="alert" style={{ background: 'var(--red-bg)', borderColor: 'transparent', boxShadow: 'none', fontSize: 14 }}>
      <div style={{ color: 'var(--red)', fontWeight: 600, marginBottom: 4 }}>Error: {message || 'API unreachable'}</div>
      <div style={{ color: 'var(--ink-2)', fontSize: 13 }}>If this persists, the System page shows which service is failing.</div>
    </div>
  )
}

export function BBCard({ title, children, style, accent }) {
  return (
    <div className="bb-card" style={style}>
      {title && (
        <div className="bb-card-header" style={accent ? { color: accent } : {}}>
          {title}
        </div>
      )}
      {children}
    </div>
  )
}

export function ScoreBadge({ score }) {
  const s = parseFloat(score) || 0
  const cls = s >= 10 ? 'pill-bull' : s <= -10 ? 'pill-bear' : 'pill-neut'
  return <span className={`score-pill ${cls}`}>{s > 0 ? '+' : ''}{s.toFixed(1)}</span>
}

export function ActionBadge({ action }) {
  const map = {
    'Strong Buy':   ['var(--green)', '▲▲'],
    'Buy':          ['var(--green)', '▲'],
    'Mild Bullish': ['var(--green)', '△'],
    'Neutral':      ['var(--yellow)', '–'],
    'Mild Bearish': ['var(--yellow)', '▽'],
    'Sell':         ['var(--red)',   '▼'],
    'Strong Sell':  ['var(--red)',   '▼▼'],
  }
  const [color, icon] = map[action] || ['var(--ink-3)', '?']
  return (
    <span style={{ color, fontWeight: 600, fontSize: 13.5, whiteSpace: 'nowrap' }}>
      <span aria-hidden="true" style={{ fontSize: 10, marginRight: 5 }}>{icon}</span>{action}
    </span>
  )
}

export function MetricCard({ label, value, sub, color, size = 'md' }) {
  return (
    <div className="bb-card" style={{ padding: '14px 16px' }}>
      <div style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--ink-3)', marginBottom: 4 }}>{label}</div>
      <div style={{ fontFamily: 'var(--font-display)', fontSize: size === 'lg' ? 28 : 22, fontWeight: 650, letterSpacing: '-.02em', color: color || 'var(--ink)' }}>{value ?? '—'}</div>
      {sub && <div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

export function ScoreBar({ score, label }) {
  const s = parseFloat(score) || 0
  const pct = Math.abs(s) / 2
  const color = s > 0 ? 'var(--green)' : s < 0 ? 'var(--red)' : 'var(--yellow)'
  return (
    <div style={{ marginBottom: 10 }}>
      {label && (
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
          <span style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>{label}</span>
          <span style={{ fontSize: 13, fontWeight: 600, color }}>{s > 0 ? '+' : ''}{s.toFixed(1)}</span>
        </div>
      )}
      <div className="bar-track">
        <div style={{ position: 'absolute', left: '50%', top: 0, width: 1, height: '100%', background: 'var(--line-2)' }} />
        <div style={{
          width: `${pct}%`, height: '100%', borderRadius: 4,
          background: color,
          position: 'absolute',
          [s >= 0 ? 'left' : 'right']: '50%',
          transition: 'width 0.5s ease',
        }} />
      </div>
    </div>
  )
}

export function SectionHeader({ children, color }) {
  return (
    <div style={{
      fontFamily: 'var(--font-display)', fontSize: 15, fontWeight: 650, letterSpacing: '-.01em',
      color: color || 'var(--ink)', marginBottom: 10,
    }}>{children}</div>
  )
}

export function SeverityBadge({ severity }) {
  const tone = { CRITICAL: 'bad', WARNING: 'warn', INFO: 'info' }[severity] || ''
  return <span className={`aria-pill ${tone}`} style={{ height: 24, fontSize: 12 }}>{severity}</span>
}

export function Empty({ message }) {
  return (
    <div style={{ color: 'var(--ink-3)', textAlign: 'center', padding: 32, fontSize: 14 }}>
      {message || 'No data'}
    </div>
  )
}

export function TabBar({ tabs, active, onChange }) {
  return (
    /* Merged pages live behind these tabs, so a tab you cannot reach is a page
       you cannot reach. The row scrolls sideways on a phone and the buttons
       refuse to be squashed. */
    <div role="tablist" style={{
      display: 'flex', gap: 4, marginBottom: 18, padding: 4, width: 'fit-content', maxWidth: '100%',
      background: 'var(--inset)', borderRadius: 999,
      overflowX: 'auto', overflowY: 'hidden', scrollbarWidth: 'none', WebkitOverflowScrolling: 'touch',
    }}>
      {tabs.map(t => {
        const on = active === t.id
        return (
          <button key={t.id} role="tab" aria-selected={on} onClick={() => onChange(t.id)}
            style={{
              height: 34, padding: '0 15px', border: 'none', borderRadius: 999,
              flexShrink: 0, whiteSpace: 'nowrap', fontSize: 13.5, fontWeight: on ? 600 : 550,
              color: on ? 'var(--ink)' : 'var(--ink-2)',
              background: on ? 'var(--sheet)' : 'transparent',
              boxShadow: on ? '0 1px 4px rgba(10,20,50,.14)' : 'none',
            }}>
            {t.icon} {t.label}
          </button>
        )
      })}
    </div>
  )
}

/* ── Score Gauge (arc SVG) ─────────────────────────────── */
export function ScoreGauge({ score, size = 140 }) {
  const s = parseFloat(score) || 0
  const norm = (s + 100) / 200  // 0 to 1
  const R = size / 2 - 12
  const cx = size / 2, cy = size / 2 + 8
  const startAngle = -210
  const sweepAngle = 240
  const toRad = d => (d * Math.PI) / 180
  const arcEnd = startAngle + sweepAngle * norm
  const x1 = cx + R * Math.cos(toRad(startAngle))
  const y1 = cy + R * Math.sin(toRad(startAngle))
  const x2 = cx + R * Math.cos(toRad(arcEnd))
  const y2 = cy + R * Math.sin(toRad(arcEnd))
  const largeArc = sweepAngle * norm > 180 ? 1 : 0
  const color = s >= 10 ? 'var(--green)' : s <= -10 ? 'var(--red)' : 'var(--yellow)'
  const trackX2 = cx + R * Math.cos(toRad(startAngle + sweepAngle))
  const trackY2 = cy + R * Math.sin(toRad(startAngle + sweepAngle))

  return (
    <svg width={size} height={size * 0.82} viewBox={`0 0 ${size} ${size * 0.82}`} className="gauge-svg">
      {/* Track */}
      <path
        d={`M${x1} ${y1} A${R} ${R} 0 1 1 ${trackX2} ${trackY2}`}
        fill="none" stroke="var(--inset)" strokeWidth={10} strokeLinecap="round"
      />
      {/* Arc */}
      {norm > 0.01 && (
        <path
          d={`M${x1} ${y1} A${R} ${R} 0 ${largeArc} 1 ${x2} ${y2}`}
          fill="none" stroke={color} strokeWidth={10} strokeLinecap="round"
        />
      )}
      {/* Score */}
      <text x={cx} y={cy - 4} textAnchor="middle" fill={color} fontSize={size * 0.2} fontWeight="650">
        {s > 0 ? '+' : ''}{s.toFixed(0)}
      </text>
      <text x={cx} y={cy + 14} textAnchor="middle" fill="var(--ink-3)" fontSize={12}>
        /100
      </text>
    </svg>
  )
}

/* ── Conflict flag row ─────────────────────────────────── */
export function ConflictRow({ conflict }) {
  const color = conflict.severity === 'HIGH' ? 'var(--red)' : conflict.severity === 'MEDIUM' ? 'var(--yellow)' : 'var(--ink-3)'
  return (
    <div style={{ borderLeft: `3px solid ${color}`, paddingLeft: 12, marginBottom: 12 }}>
      <div style={{ fontSize: 13.5, color, fontWeight: 600, marginBottom: 2 }}>
        {conflict.severity} · {conflict.description?.slice(0, 80)}
      </div>
      <div style={{ fontSize: 13, color: 'var(--ink-2)' }}>{conflict.impact?.slice(0, 100)}</div>
    </div>
  )
}
