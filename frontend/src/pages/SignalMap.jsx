import React, { useEffect, useRef, useState, useCallback } from 'react'
import axios from 'axios'

const mono = { fontFamily: 'var(--mono)' }

const nodeColor = (score) =>
  score > 25 ? '#22d3ee' : score > 8 ? '#34d399' : score < -25 ? '#ff3860' :
  score < -8 ? '#f472b6' : '#fbbf24'

// ─── circular score gauge ─────────────────────────────────────────────────────
function Gauge({ score }) {
  const norm = Math.round((score + 100) / 2)
  const c = nodeColor(score), R = 26, circ = 2 * Math.PI * R
  const frac = Math.max(0, Math.min(1, norm / 100))
  return (
    <svg width="64" height="64" viewBox="0 0 64 64">
      <circle cx="32" cy="32" r={R} fill="none" stroke="#141b2a" strokeWidth="5" />
      <circle cx="32" cy="32" r={R} fill="none" stroke={c} strokeWidth="5" strokeLinecap="round"
        strokeDasharray={circ} strokeDashoffset={circ * (1 - frac)} transform="rotate(-90 32 32)"
        style={{ filter: `drop-shadow(0 0 4px ${c})` }} />
      <text x="32" y="37" textAnchor="middle" fill={c} fontSize="16" fontWeight="800" fontFamily="var(--mono)">{norm}</text>
    </svg>
  )
}

// ─── Obsidian-style force-directed graph ──────────────────────────────────────
function Graph({ signals, onHover }) {
  const ref = useRef(null)
  const sim = useRef({ nodes: [], links: [], adj: new Map() })
  const view = useRef({ scale: 1, ox: 0, oy: 0, mx: -1, my: -1, drag: null, pan: null })

  // build graph: MARKET hub → asset-class hubs → tickers
  useEffect(() => {
    const arr = Object.values(signals || {})
    if (!arr.length) return
    const nodes = [], links = [], adj = new Map()
    const add = (n) => { nodes.push(n); adj.set(n.id, new Set()); return n }
    const link = (a, b) => { links.push([a, b]); adj.get(a)?.add(b); adj.get(b)?.add(a) }

    const center = add({ id: '★MARKET', label: 'MARKET', hub: true, r: 9, color: '#e8eef6', score: null, x: 0, y: 0, vx: 0, vy: 0, fx: 0, fy: 0 })
    const classes = {}
    const cls = (c) => (c || 'other').toLowerCase().replace('equities', 'equity')
    arr.forEach(s => {
      const cn = cls(s.asset_class)
      if (!classes[cn]) {
        classes[cn] = add({ id: '§' + cn, label: cn.toUpperCase(), hub: true, r: 6, color: '#9fb2cc', score: null,
          x: (Math.random() - 0.5) * 200, y: (Math.random() - 0.5) * 200, vx: 0, vy: 0 })
        link('★MARKET', '§' + cn)
      }
    })
    arr.forEach(s => {
      const cn = cls(s.asset_class), strength = Math.min(Math.abs(s.composite_score || 0), 100) / 100
      const n = add({ id: s.ticker, label: s.ticker, hub: false, s, score: s.composite_score || 0,
        r: 3 + strength * 5, color: nodeColor(s.composite_score || 0),
        x: (Math.random() - 0.5) * 400, y: (Math.random() - 0.5) * 400, vx: 0, vy: 0 })
      link('§' + cn, s.ticker)
    })
    // link tickers that move together strongly (same-sign, both high |score|)
    const strong = arr.filter(s => Math.abs(s.composite_score) > 25)
    for (let i = 0; i < strong.length; i++)
      for (let j = i + 1; j < strong.length; j++)
        if (Math.sign(strong[i].composite_score) === Math.sign(strong[j].composite_score))
          link(strong[i].ticker, strong[j].ticker)

    sim.current = { nodes, links, adj, byId: new Map(nodes.map(n => [n.id, n])) }
  }, [signals])

  useEffect(() => {
    const cv = ref.current, cx = cv.getContext('2d')
    let raf, W, H
    const resize = () => { const r = cv.getBoundingClientRect(); W = cv.width = r.width; H = cv.height = r.height }
    resize(); window.addEventListener('resize', resize)
    const hexA = (h, a) => { const n = parseInt(h.slice(1), 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})` }

    const step = () => {
      const { nodes, links, byId } = sim.current
      if (!nodes.length) return
      // repulsion (all pairs)
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i]
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j]
          let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy || 0.01
          const f = 1400 / d2, d = Math.sqrt(d2)
          const fx = (dx / d) * f, fy = (dy / d) * f
          a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy
        }
      }
      // spring along links
      for (const [ai, bi] of links) {
        const a = byId.get(ai), b = byId.get(bi); if (!a || !b) continue
        let dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 0.01
        const target = (a.hub && b.hub) ? 120 : a.hub || b.hub ? 78 : 60
        const f = (d - target) * 0.02
        const fx = (dx / d) * f, fy = (dy / d) * f
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy
      }
      // centering + damping + integrate
      for (const n of nodes) {
        if (n.id === '★MARKET') { n.x = 0; n.y = 0; n.vx = 0; n.vy = 0; continue }
        n.vx += -n.x * 0.002; n.vy += -n.y * 0.002
        n.vx *= 0.86; n.vy *= 0.86
        if (view.current.drag === n) continue
        n.x += n.vx; n.y += n.vy
      }
    }

    const draw = () => {
      step()
      const { nodes, links, byId, adj } = sim.current
      const v = view.current
      cx.clearRect(0, 0, W, H)
      cx.save(); cx.translate(W / 2 + v.ox, H / 2 + v.oy); cx.scale(v.scale, v.scale)

      // hover detection (world coords)
      const wx = (v.mx - W / 2 - v.ox) / v.scale, wy = (v.my - H / 2 - v.oy) / v.scale
      let hov = null, hd = 14 / v.scale
      for (const n of nodes) { const d = Math.hypot(n.x - wx, n.y - wy); if (d < Math.max(n.r + 4, hd)) { hov = n; hd = d } }
      const nb = hov ? adj.get(hov.id) : null

      // links
      for (const [ai, bi] of links) {
        const a = byId.get(ai), b = byId.get(bi); if (!a || !b) continue
        const lit = hov && (ai === hov.id || bi === hov.id)
        cx.beginPath(); cx.moveTo(a.x, a.y); cx.lineTo(b.x, b.y)
        cx.strokeStyle = lit ? 'rgba(180,210,240,0.5)' : hov ? 'rgba(120,140,170,0.06)' : 'rgba(120,140,170,0.14)'
        cx.lineWidth = lit ? 1.4 : 1; cx.stroke()
      }
      // nodes
      for (const n of nodes) {
        const dim = hov && n !== hov && !(nb && nb.has(n.id))
        const glow = n.hub ? 0.3 : 0.5
        if (!dim) { const g = cx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r * 3)
          g.addColorStop(0, hexA(n.color, glow)); g.addColorStop(1, hexA(n.color, 0))
          cx.beginPath(); cx.arc(n.x, n.y, n.r * 3, 0, 6.29); cx.fillStyle = g; cx.fill() }
        cx.beginPath(); cx.arc(n.x, n.y, n.r, 0, 6.29)
        cx.fillStyle = dim ? hexA(n.color, 0.18) : n.color; cx.fill()
        if (n.hub) { cx.lineWidth = 1; cx.strokeStyle = hexA('#ffffff', dim ? 0.1 : 0.5); cx.stroke() }
        // labels: hubs always, tickers when strong / hovered / neighbour
        const showLabel = n.hub || Math.abs(n.score) > 28 || n === hov || (nb && nb.has(n.id))
        if (showLabel && !dim) {
          cx.font = (n.hub ? 'bold ' : '') + Math.round(10) + 'px monospace'
          cx.fillStyle = hexA(n.hub ? '#cfe0f4' : n.color, 0.92); cx.textAlign = 'center'
          cx.fillText(n.label, n.x, n.y - n.r - 5)
        }
      }
      cx.restore()
      onHover(hov && !hov.hub ? hov.s : null)
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)

    // interaction: pan, zoom, drag nodes
    const pos = (e) => { const r = cv.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top } }
    const down = (e) => {
      const p = pos(e); view.current.mx = p.x; view.current.my = p.y
      const v = view.current, wx = (p.x - W / 2 - v.ox) / v.scale, wy = (p.y - H / 2 - v.oy) / v.scale
      let hit = null, hd = 12 / v.scale
      for (const n of sim.current.nodes) { const d = Math.hypot(n.x - wx, n.y - wy); if (d < Math.max(n.r + 4, hd) && n.id !== '★MARKET') { hit = n; hd = d } }
      if (hit) v.drag = hit; else v.pan = { x: p.x, y: p.y, ox: v.ox, oy: v.oy }
    }
    const move = (e) => {
      const p = pos(e), v = view.current; v.mx = p.x; v.my = p.y
      if (v.drag) { v.drag.x = (p.x - W / 2 - v.ox) / v.scale; v.drag.y = (p.y - H / 2 - v.oy) / v.scale; v.drag.vx = v.drag.vy = 0 }
      else if (v.pan) { v.ox = v.pan.ox + (p.x - v.pan.x); v.oy = v.pan.oy + (p.y - v.pan.y) }
    }
    const up = () => { view.current.drag = null; view.current.pan = null }
    const leave = () => { view.current.mx = -1; view.current.my = -1; up() }
    const wheel = (e) => { e.preventDefault(); const v = view.current; v.scale = Math.max(0.4, Math.min(2.6, v.scale * (e.deltaY < 0 ? 1.1 : 0.9))) }
    cv.addEventListener('mousedown', down); window.addEventListener('mousemove', move); window.addEventListener('mouseup', up)
    cv.addEventListener('mouseleave', leave); cv.addEventListener('wheel', wheel, { passive: false })
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', resize); window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); cv.removeEventListener('mousedown', down); cv.removeEventListener('wheel', wheel); cv.removeEventListener('mouseleave', leave) }
  }, [onHover])

  return <canvas ref={ref} style={{ width: '100%', height: '100%', display: 'block', cursor: 'grab' }} />
}

// ─── inspector ────────────────────────────────────────────────────────────────
function Inspector({ s }) {
  if (!s) return <div style={{ ...mono, fontSize: 10, color: '#3a4a60', textAlign: 'center', paddingTop: 30 }}>hover a node</div>
  const c = nodeColor(s.composite_score)
  const row = (k, v, col) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, ...mono, padding: '3px 0', borderBottom: '1px solid #141b2a' }}>
      <span style={{ color: '#5a6a82' }}>{k}</span><span style={{ color: col || '#c8d6e8' }}>{v}</span>
    </div>
  )
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <Gauge score={s.composite_score} />
        <div><div style={{ ...mono, fontSize: 15, fontWeight: 800, color: '#fff' }}>{s.ticker}</div>
          <div style={{ ...mono, fontSize: 10, fontWeight: 700, color: c }}>{(s.action || '').toUpperCase()}</div></div>
      </div>
      {row('SCORE', (s.composite_score >= 0 ? '+' : '') + (s.composite_score?.toFixed?.(1) ?? s.composite_score), c)}
      {row('CONFIDENCE', s.confidence)}
      {row('BULLISH', ((s.bullish_prob ?? 0) * 100).toFixed(0) + '%')}
      {row('PRICE', '$' + (s.current_price?.toFixed?.(2) ?? s.current_price))}
      {row('CLASS', s.asset_class)}
      {(s.drivers?.length) ? <div style={{ ...mono, fontSize: 9, color: '#5a6a82', marginTop: 8, lineHeight: 1.5 }}>▸ {s.drivers[0]}</div> : null}
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

  return (
    <div style={{ position: 'relative', height: 'calc(100vh - 68px)', background: 'radial-gradient(900px 600px at 50% 45%, #0a1020 0%, #060912 60%, #04060c 100%)', borderRadius: 10, border: '1px solid #1a2438', overflow: 'hidden' }}>
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, display: 'flex', alignItems: 'center', gap: 14, padding: '12px 18px', zIndex: 3, borderBottom: '1px solid #141b2a', background: 'rgba(8,12,20,0.6)' }}>
        <span style={{ ...mono, fontSize: 15, fontWeight: 800, letterSpacing: 3, color: '#7dd3fc', textShadow: '0 0 12px rgba(125,211,252,0.5)' }}>ARIA · SIGNAL GRAPH</span>
        <span style={{ ...mono, fontSize: 10, color: '#5a6a82' }}>{arr.length} SIGNALS · force-directed</span>
        <span style={{ marginLeft: 'auto', ...mono, fontSize: 10, color: '#5a6a82' }}>drag nodes · scroll to zoom · drag bg to pan</span>
      </div>

      <Graph signals={signals} onHover={onHover} />

      <div style={{ position: 'absolute', top: 60, left: 16, width: 205, padding: 14, zIndex: 3, background: 'rgba(8,12,20,0.85)', border: `1px solid ${hover ? nodeColor(hover.composite_score) : '#1a2438'}`, borderRadius: 8, backdropFilter: 'blur(4px)', transition: 'border-color .2s' }}>
        <div style={{ ...mono, fontSize: 9, fontWeight: 800, letterSpacing: 2, color: '#7dd3fc', marginBottom: 10 }}>◈ INSPECTOR</div>
        <Inspector s={hover} />
      </div>

      <div style={{ position: 'absolute', bottom: 14, left: 0, right: 0, display: 'flex', justifyContent: 'center', gap: 16, zIndex: 3, ...mono, fontSize: 9, color: '#5a6a82' }}>
        <span><span style={{ color: '#e8eef6' }}>◉</span> market / class hubs</span>
        <span><span style={{ color: '#22d3ee' }}>●</span> bull</span>
        <span><span style={{ color: '#fbbf24' }}>●</span> neutral</span>
        <span><span style={{ color: '#ff3860' }}>●</span> bear</span>
        <span style={{ color: '#3a4a60' }}>· lines = shared class / co-movement · size = conviction</span>
      </div>
    </div>
  )
}
