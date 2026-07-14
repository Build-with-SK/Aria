/**
 * UI.jsx — Bloomberg Terminal component library
 */
import React from 'react'

export function Spinner() {
  return (
    <div style={{ display:'flex', alignItems:'center', justifyContent:'center', padding: 40, gap: 8, fontFamily:'var(--mono)', color:'var(--orange)', fontSize:12 }}>
      <div style={{ width:10, height:10, background:'var(--orange)', borderRadius:'50%', animation:'blink 0.6s infinite' }} />
      <div style={{ width:10, height:10, background:'var(--orange)', borderRadius:'50%', animation:'blink 0.6s 0.2s infinite' }} />
      <div style={{ width:10, height:10, background:'var(--orange)', borderRadius:'50%', animation:'blink 0.6s 0.4s infinite' }} />
      <span style={{ marginLeft:8 }}>LOADING DATA</span>
    </div>
  )
}

export function ErrorBox({ message }) {
  return (
    <div className="bb-card" style={{ borderColor:'var(--red)', color:'var(--red)', fontFamily:'var(--mono)', fontSize:12 }}>
      <div style={{ marginBottom:6 }}>⚠ ERROR: {message || 'API unreachable'}</div>
      <div style={{ color:'var(--muted)', fontSize:11 }}>Start backend: uvicorn backend.main:app --reload --port 8000</div>
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
    'Mild Bullish': ['#44aa66',      '△'],
    'Neutral':      ['var(--yellow)','─'],
    'Mild Bearish': ['#cc7733',      '▽'],
    'Sell':         ['var(--red)',   '▼'],
    'Strong Sell':  ['var(--red)',   '▼▼'],
  }
  const [color, icon] = map[action] || ['var(--muted)', '?']
  return (
    <span style={{ color, fontFamily:'var(--mono)', fontWeight:700, fontSize:12, letterSpacing:'0.05em' }}>
      {icon} {action?.toUpperCase()}
    </span>
  )
}

export function MetricCard({ label, value, sub, color, size = 'md' }) {
  return (
    <div className="bb-card" style={{ padding:'10px 12px' }}>
      <div style={{ fontSize:9, fontWeight:700, textTransform:'uppercase', letterSpacing:'0.1em', color:'var(--muted)', marginBottom:4, fontFamily:'var(--mono)' }}>{label}</div>
      <div style={{ fontSize: size === 'lg' ? 22 : 16, fontWeight:700, fontFamily:'var(--mono)', color: color || 'var(--white)' }}>{value ?? '—'}</div>
      {sub && <div style={{ fontSize:10, color:'var(--muted)', marginTop:2, fontFamily:'var(--mono)' }}>{sub}</div>}
    </div>
  )
}

export function ScoreBar({ score, label }) {
  const s = parseFloat(score) || 0
  const pct = Math.abs(s) / 2
  const color = s > 0 ? 'var(--green)' : s < 0 ? 'var(--red)' : 'var(--yellow)'
  return (
    <div style={{ marginBottom: 8 }}>
      {label && (
        <div style={{ display:'flex', justifyContent:'space-between', marginBottom:3 }}>
          <span style={{ fontSize:10, color:'var(--muted)', fontFamily:'var(--mono)', textTransform:'uppercase', letterSpacing:'0.06em' }}>{label}</span>
          <span style={{ fontSize:11, fontFamily:'var(--mono)', fontWeight:700, color }}>{s > 0 ? '+' : ''}{s.toFixed(1)}</span>
        </div>
      )}
      <div className="bar-track">
        <div style={{ position:'absolute', left:'50%', top:0, width:1, height:'100%', background:'#333' }} />
        <div style={{
          width:`${pct}%`, height:'100%', borderRadius:1,
          background: color,
          position:'absolute',
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
      fontSize:9, fontWeight:700, textTransform:'uppercase', letterSpacing:'0.12em',
      color: color || 'var(--orange)',
      borderBottom:'1px solid var(--border-2)',
      paddingBottom:5, marginBottom:10,
      fontFamily:'var(--mono)',
    }}>{children}</div>
  )
}

export function SeverityBadge({ severity }) {
  const map = { CRITICAL:['var(--red)','▲'], WARNING:['var(--yellow)','!'], INFO:['var(--blue)','i'] }
  const [color, icon] = map[severity] || ['var(--muted)','?']
  return <span style={{ color, fontFamily:'var(--mono)', fontWeight:700, fontSize:11 }}>[{icon}] {severity}</span>
}

export function Empty({ message }) {
  return (
    <div style={{ color:'var(--muted)', textAlign:'center', padding:32, fontFamily:'var(--mono)', fontSize:12 }}>
      {message || 'NO DATA'}
    </div>
  )
}

export function TabBar({ tabs, active, onChange }) {
  return (
    <div style={{ display:'flex', borderBottom:'1px solid var(--border-2)', marginBottom:16, gap:0 }}>
      {tabs.map(t => (
        <button key={t.id} onClick={() => onChange(t.id)}
          style={{
            padding:'7px 16px', background:'none', border:'none', cursor:'pointer',
            fontFamily:'var(--mono)', fontSize:11, fontWeight:600, letterSpacing:'0.06em',
            textTransform:'uppercase',
            color: active === t.id ? 'var(--orange)' : 'var(--muted)',
            borderBottom: active === t.id ? '2px solid var(--orange)' : '2px solid transparent',
            transition: 'color 0.15s',
          }}>
          {t.icon} {t.label}
        </button>
      ))}
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
  const color = s >= 40 ? '#00cc44' : s >= 10 ? '#44aa44' : s <= -40 ? '#ff3333' : s <= -10 ? '#cc3333' : '#ffcc00'
  const trackX2 = cx + R * Math.cos(toRad(startAngle + sweepAngle))
  const trackY2 = cy + R * Math.sin(toRad(startAngle + sweepAngle))

  return (
    <svg width={size} height={size * 0.82} viewBox={`0 0 ${size} ${size * 0.82}`} className="gauge-svg">
      {/* Track */}
      <path
        d={`M${x1} ${y1} A${R} ${R} 0 1 1 ${trackX2} ${trackY2}`}
        fill="none" stroke="#1a1a1a" strokeWidth={10} strokeLinecap="round"
      />
      {/* Arc */}
      {norm > 0.01 && (
        <path
          d={`M${x1} ${y1} A${R} ${R} 0 ${largeArc} 1 ${x2} ${y2}`}
          fill="none" stroke={color} strokeWidth={10} strokeLinecap="round"
          style={{ filter:`drop-shadow(0 0 6px ${color})` }}
        />
      )}
      {/* Score */}
      <text x={cx} y={cy - 4} textAnchor="middle" fill={color} fontSize={size * 0.2} fontWeight="700" fontFamily="var(--mono)">
        {s > 0 ? '+' : ''}{s.toFixed(0)}
      </text>
      <text x={cx} y={cy + 14} textAnchor="middle" fill="var(--muted)" fontSize={10} fontFamily="var(--mono)">
        /100
      </text>
    </svg>
  )
}

/* ── Conflict flag row ─────────────────────────────────── */
export function ConflictRow({ conflict }) {
  const color = conflict.severity === 'HIGH' ? 'var(--red)' : conflict.severity === 'MEDIUM' ? 'var(--yellow)' : 'var(--muted)'
  return (
    <div style={{ borderLeft:`2px solid ${color}`, paddingLeft:10, marginBottom:10 }}>
      <div style={{ fontSize:10, fontFamily:'var(--mono)', color, fontWeight:700, marginBottom:2 }}>
        [{conflict.severity}] {conflict.description?.slice(0, 80)}
      </div>
      <div style={{ fontSize:10, color:'var(--muted)', fontFamily:'var(--mono)' }}>{conflict.impact?.slice(0, 100)}</div>
    </div>
  )
}
