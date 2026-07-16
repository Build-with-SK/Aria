import React, { useEffect, useRef, useState, useCallback } from 'react'
import axios from 'axios'

const mono = { fontFamily: 'var(--mono)' }

// deterministic pseudo-random from a string (stable node placement)
function seed(str) {
  let h = 2166136261
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619) }
  return () => { h += 0x6D2B79F5; let t = h; t = Math.imul(t ^ t >>> 15, t | 1); t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296 }
}

const nodeColor = (score) =>
  score > 25 ? '#22d3ee' : score > 8 ? '#34d399' : score < -25 ? '#ff3860' :
  score < -8 ? '#f472b6' : '#fbbf24'

// ─── circular score gauge (the reel's "73" dial) ─────────────────────────────
function Gauge({ score }) {
  const norm = Math.round((score + 100) / 2)          // -100..100 → 0..100
  const c = nodeColor(score), R = 26, circ = 2 * Math.PI * R
  const frac = Math.max(0, Math.min(1, norm / 100))
  return (
    <svg width="64" height="64" viewBox="0 0 64 64">
      <circle cx="32" cy="32" r={R} fill="none" stroke="#2a0e12" strokeWidth="5" />
      <circle cx="32" cy="32" r={R} fill="none" stroke={c} strokeWidth="5" strokeLinecap="round"
        strokeDasharray={circ} strokeDashoffset={circ * (1 - frac)} transform="rotate(-90 32 32)"
        style={{ filter: `drop-shadow(0 0 4px ${c})` }} />
      <text x="32" y="37" textAnchor="middle" fill={c} fontSize="16" fontWeight="800" fontFamily="var(--mono)">{norm}</text>
    </svg>
  )
}

// ─── the constellation canvas ─────────────────────────────────────────────────
function Constellation({ signals, onHover, hovered }) {
  const ref = useRef(null)
  const nodesRef = useRef([])
  const mouse = useRef({ x: -1, y: -1 })

  // build nodes when signals change
  useEffect(() => {
    const arr = Object.values(signals || {})
    nodesRef.current = arr.map((s) => {
      const rnd = seed(s.ticker || 'x')
      const ang = rnd() * Math.PI * 2
      const strength = Math.min(Math.abs(s.composite_score || 0), 100) / 100
      // strong signals sit further out in the "firework"; weak ones hug the core
      const rad = 70 + (0.25 + strength * 0.75) * 220 * (0.6 + rnd() * 0.5)
      return {
        s, ang, rad, wob: rnd() * 6.28,
        size: 2.2 + strength * 5.5,
        color: nodeColor(s.composite_score || 0),
        bright: s.confidence === 'High' ? 1 : s.confidence === 'Medium' ? 0.7 : 0.45,
        x: 0, y: 0,
      }
    })
  }, [signals])

  useEffect(() => {
    const cv = ref.current, cx = cv.getContext('2d')
    let raf, W, H
    const resize = () => { const r = cv.getBoundingClientRect(); W = cv.width = r.width; H = cv.height = r.height }
    resize(); window.addEventListener('resize', resize)

    // ambient dust
    const dust = Array.from({ length: 150 }, () => ({ a: Math.random() * 6.28, r: 40 + Math.random() * 300, s: 0.0006 + Math.random() * 0.0016, z: Math.random() }))
    const hexA = (h, a) => { const n = parseInt(h.slice(1), 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})` }
    let t0 = performance.now()

    const draw = (now) => {
      const t = (now - t0) / 1000
      const CX = W / 2, CY = H / 2
      cx.clearRect(0, 0, W, H)

      // faint ring guides
      for (let i = 1; i <= 3; i++) { cx.beginPath(); cx.arc(CX, CY, 90 * i, 0, 6.29); cx.strokeStyle = 'rgba(255,40,60,0.05)'; cx.lineWidth = 1; cx.stroke() }

      // dust
      for (const d of dust) { d.a += d.s; const x = CX + Math.cos(d.a) * d.r, y = CY + Math.sin(d.a) * d.r * 0.82
        cx.beginPath(); cx.arc(x, y, 0.7 + d.z, 0, 6.29); cx.fillStyle = `rgba(255,${60 + d.z * 80},${70 + d.z * 40},${0.12 + d.z * 0.2})`; cx.fill() }

      // glowing core
      const pulse = 1 + 0.08 * Math.sin(t * 2)
      const g = cx.createRadialGradient(CX, CY, 2, CX, CY, 60 * pulse)
      g.addColorStop(0, 'rgba(255,240,240,0.95)'); g.addColorStop(0.25, 'rgba(255,40,60,0.7)'); g.addColorStop(1, 'rgba(255,0,20,0)')
      cx.beginPath(); cx.arc(CX, CY, 60 * pulse, 0, 6.29); cx.fillStyle = g; cx.fill()

      // nodes
      let near = null, nd = 16
      for (const n of nodesRef.current) {
        const wob = Math.sin(t * 0.6 + n.wob) * 4
        const x = CX + Math.cos(n.ang) * (n.rad + wob), y = CY + Math.sin(n.ang) * (n.rad + wob) * 0.82
        n.x = x; n.y = y
        // connective line to core for strong signals
        if (Math.abs(n.s.composite_score) > 30) { cx.beginPath(); cx.moveTo(CX, CY); cx.lineTo(x, y); cx.strokeStyle = hexA(n.color, 0.08); cx.lineWidth = 1; cx.stroke() }
        // glow
        const gg = cx.createRadialGradient(x, y, 0, x, y, n.size * 3.5)
        gg.addColorStop(0, hexA(n.color, 0.5 * n.bright)); gg.addColorStop(1, hexA(n.color, 0))
        cx.beginPath(); cx.arc(x, y, n.size * 3.5, 0, 6.29); cx.fillStyle = gg; cx.fill()
        cx.beginPath(); cx.arc(x, y, n.size, 0, 6.29); cx.fillStyle = hexA(n.color, 0.9 * n.bright); cx.fill()
        // hover proximity
        const dm = Math.hypot(mouse.current.x - x, mouse.current.y - y)
        if (dm < nd) { near = n; nd = dm }
        // label strong nodes
        if (Math.abs(n.s.composite_score) > 28 || n === near) {
          cx.font = '9px monospace'; cx.fillStyle = hexA(n.color, 0.85); cx.textAlign = 'center'
          cx.fillText(n.s.ticker, x, y - n.size - 5)
        }
      }
      // ring highlight on hovered
      if (near) { cx.beginPath(); cx.arc(near.x, near.y, near.size + 6, 0, 6.29); cx.strokeStyle = near.color; cx.lineWidth = 1.5; cx.stroke() }
      onHover(near ? near.s : null, near ? { x: near.x, y: near.y } : null)
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)

    const mv = (e) => { const r = cv.getBoundingClientRect(); mouse.current = { x: e.clientX - r.left, y: e.clientY - r.top } }
    const lv = () => { mouse.current = { x: -1, y: -1 } }
    cv.addEventListener('mousemove', mv); cv.addEventListener('mouseleave', lv)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', resize); cv.removeEventListener('mousemove', mv); cv.removeEventListener('mouseleave', lv) }
  }, [onHover])

  return <canvas ref={ref} style={{ width: '100%', height: '100%', display: 'block', cursor: 'crosshair' }} />
}

// ─── inspector card ───────────────────────────────────────────────────────────
function Inspector({ s }) {
  if (!s) return (
    <div style={{ ...mono, fontSize: 10, color: '#5a2a30', textAlign: 'center', paddingTop: 40 }}>
      hover a node to inspect
    </div>
  )
  const c = nodeColor(s.composite_score)
  const row = (k, v, col) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, ...mono, padding: '3px 0', borderBottom: '1px solid #1a0a0c' }}>
      <span style={{ color: '#8a4a52' }}>{k}</span><span style={{ color: col || '#e8c0c6' }}>{v}</span>
    </div>
  )
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <Gauge score={s.composite_score} />
        <div>
          <div style={{ ...mono, fontSize: 15, fontWeight: 800, color: '#fff' }}>{s.ticker}</div>
          <div style={{ ...mono, fontSize: 10, fontWeight: 700, color: c }}>{(s.action || '').toUpperCase()}</div>
        </div>
      </div>
      {row('SCORE', (s.composite_score >= 0 ? '+' : '') + (s.composite_score?.toFixed?.(1) ?? s.composite_score), c)}
      {row('CONFIDENCE', s.confidence)}
      {row('BULLISH PROB', ((s.bullish_prob ?? 0) * 100).toFixed(0) + '%')}
      {row('PRICE', '$' + (s.current_price?.toFixed?.(2) ?? s.current_price))}
      {row('CLASS', s.asset_class)}
      {s.stop_loss ? row('STOP', '$' + s.stop_loss?.toFixed?.(2), '#ff3860') : null}
      {s.take_profit ? row('TARGET', '$' + s.take_profit?.toFixed?.(2), '#34d399') : null}
      {(s.drivers?.length) ? <div style={{ ...mono, fontSize: 9, color: '#8a5a60', marginTop: 8, lineHeight: 1.5 }}>▸ {s.drivers[0]}</div> : null}
    </div>
  )
}

// ─── page ─────────────────────────────────────────────────────────────────────
export default function SignalMap() {
  const [data, setData] = useState(null)
  const [hover, setHover] = useState(null)
  const hoverRef = useRef(null)
  const onHover = useCallback((s) => { if (s?.ticker !== hoverRef.current?.ticker) { hoverRef.current = s; setHover(s) } }, [])

  useEffect(() => {
    const load = () => axios.get('/api/signals').then(r => setData(r.data)).catch(() => {})
    load(); const id = setInterval(load, 30000); return () => clearInterval(id)
  }, [])

  const signals = data?.signals || {}
  const arr = Object.values(signals)
  const top = [...arr].sort((a, b) => Math.abs(b.composite_score) - Math.abs(a.composite_score))[0]

  const stat = (k, v, c) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', ...mono, fontSize: 10, padding: '4px 0', borderBottom: '1px solid #1a0a0c' }}>
      <span style={{ color: '#8a4a52' }}>{k}</span><span style={{ color: c || '#e8c0c6', fontWeight: 700 }}>{v}</span>
    </div>
  )

  return (
    <div style={{ position: 'relative', height: 'calc(100vh - 68px)', background: 'radial-gradient(1000px 700px at 50% 45%, #14060a 0%, #060305 60%, #030203 100%)', borderRadius: 10, border: '1px solid #3a1015', overflow: 'hidden' }}>
      {/* top bar */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, display: 'flex', alignItems: 'center', gap: 14, padding: '12px 18px', zIndex: 3, borderBottom: '1px solid #2a0c10', background: 'rgba(10,3,5,0.6)' }}>
        <span style={{ ...mono, fontSize: 15, fontWeight: 800, letterSpacing: 3, color: '#ff3860', textShadow: '0 0 12px rgba(255,56,96,0.6)' }}>ARIA · SIGNAL MAP</span>
        <span style={{ ...mono, fontSize: 10, color: '#8a4a52' }}>ALL SYMBOLS · {arr.length} SIGNALS</span>
        <span style={{ marginLeft: 'auto', ...mono, fontSize: 10, color: '#8a4a52' }}>hover to inspect · live</span>
      </div>

      <Constellation signals={signals} onHover={onHover} hovered={hover} />

      {/* inspector (top-left panel) */}
      <div style={{ position: 'absolute', top: 60, left: 16, width: 210, padding: 14, zIndex: 3, background: 'rgba(12,3,5,0.82)', border: `1px solid ${hover ? nodeColor(hover.composite_score) : '#3a1015'}`, borderRadius: 8, backdropFilter: 'blur(4px)', transition: 'border-color .2s' }}>
        <div style={{ ...mono, fontSize: 9, fontWeight: 800, letterSpacing: 2, color: '#ff3860', marginBottom: 10 }}>◈ INSPECTOR</div>
        <Inspector s={hover} />
      </div>

      {/* aggregate stats (top-right panel) */}
      <div style={{ position: 'absolute', top: 60, right: 16, width: 190, padding: 14, zIndex: 3, background: 'rgba(12,3,5,0.82)', border: '1px solid #3a1015', borderRadius: 8, backdropFilter: 'blur(4px)' }}>
        <div style={{ ...mono, fontSize: 9, fontWeight: 800, letterSpacing: 2, color: '#ff3860', marginBottom: 10 }}>◧ BOOK</div>
        {stat('SIGNALS', arr.length)}
        {stat('BULLISH', data?.bullish ?? '—', '#34d399')}
        {stat('BEARISH', data?.bearish ?? '—', '#ff3860')}
        {stat('NEUTRAL', data?.neutral ?? '—', '#fbbf24')}
        {stat('TOP SYMBOL', top?.ticker ?? '—', '#fff')}
        {stat('TOP SCORE', top ? (top.composite_score >= 0 ? '+' : '') + top.composite_score.toFixed(1) : '—', nodeColor(top?.composite_score ?? 0))}
      </div>

      {/* legend */}
      <div style={{ position: 'absolute', bottom: 14, left: 0, right: 0, display: 'flex', justifyContent: 'center', gap: 16, zIndex: 3, ...mono, fontSize: 9, color: '#8a4a52' }}>
        <span><span style={{ color: '#22d3ee' }}>●</span> strong bull</span>
        <span><span style={{ color: '#34d399' }}>●</span> bull</span>
        <span><span style={{ color: '#fbbf24' }}>●</span> neutral</span>
        <span><span style={{ color: '#f472b6' }}>●</span> bear</span>
        <span><span style={{ color: '#ff3860' }}>●</span> strong bear</span>
        <span style={{ color: '#5a2a30' }}>· node size = conviction · distance = strength</span>
      </div>
    </div>
  )
}
