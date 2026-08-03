/**
 * pages/Overview.jsx — THE COMMAND DECK
 * ARIA's landing view: a living crimson core, the market's pulse around it.
 * Every number is live from the engine. Research & education only.
 */
import React, { useEffect, useRef, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import axios from 'axios'

const mono = { fontFamily: 'var(--mono)' }

/* ═══ the reactor core — canvas particle heart of the deck ═══ */
function ReactorCore({ intensity = 0.6, size = 240 }) {
  const ref = useRef(null)
  const level = useRef(intensity)
  level.current = intensity

  useEffect(() => {
    const cv = ref.current, cx = cv.getContext('2d')
    const S = size, C = S / 2
    const P = Array.from({ length: 64 }, () => ({
      a: Math.random() * 6.28, r: C * 0.28 + Math.random() * C * 0.55,
      s: 0.002 + Math.random() * 0.010, z: Math.random(),
    }))
    const hexA = (a) => `rgba(255,36,71,${a})`
    let raf, t0 = performance.now()
    const draw = (now) => {
      const t = (now - t0) / 1000, lv = level.current
      cx.clearRect(0, 0, S, S)
      const breath = 1 + 0.045 * Math.sin(t * 2 * (0.5 + lv))
      // rings
      for (let i = 0; i < 5; i++) {
        const r = (C * 0.22 + i * C * 0.13) * breath
        cx.beginPath(); cx.arc(C, C, r, 0, 6.29)
        cx.strokeStyle = hexA(0.30 - i * 0.05); cx.lineWidth = i === 0 ? 2 : 1; cx.stroke()
      }
      // rotating arc segments
      for (let i = 0; i < 3; i++) {
        const r = (C * 0.30 + i * C * 0.13) * breath
        const off = t * (0.7 + i * 0.45) * (0.5 + lv) * (i % 2 ? -1 : 1)
        cx.beginPath(); cx.arc(C, C, r, off, off + 1.2 + i * 0.5)
        cx.strokeStyle = hexA(0.85); cx.lineWidth = 2.4; cx.lineCap = 'round'; cx.stroke()
        cx.beginPath(); cx.arc(C, C, r, off + Math.PI, off + Math.PI + 0.35)
        cx.strokeStyle = 'rgba(255,180,190,0.5)'; cx.lineWidth = 1.3; cx.stroke()
      }
      // particles
      for (const p of P) {
        p.a += p.s * (0.4 + lv)
        const x = C + Math.cos(p.a) * p.r * breath, y = C + Math.sin(p.a) * p.r * breath
        cx.beginPath(); cx.arc(x, y, 0.8 + p.z * 1.6, 0, 6.29)
        cx.fillStyle = hexA(0.16 + 0.55 * p.z); cx.fill()
      }
      // nucleus
      const g = cx.createRadialGradient(C, C, 1, C, C, C * 0.30 * breath)
      g.addColorStop(0, 'rgba(255,235,238,0.95)')
      g.addColorStop(0.35, hexA(0.6))
      g.addColorStop(1, 'rgba(0,0,0,0)')
      cx.beginPath(); cx.arc(C, C, C * 0.30 * breath, 0, 6.29); cx.fillStyle = g; cx.fill()
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [size])

  return <canvas ref={ref} width={size} height={size} style={{ width: size, height: size }} />
}

/* ═══ animated counter ═══ */
function Count({ value, decimals = 0, prefix = '', suffix = '' }) {
  const [v, setV] = useState(0)
  useEffect(() => {
    if (value == null) return
    const target = Number(value); const start = performance.now(); const dur = 900
    let raf
    const tick = (now) => {
      const p = Math.min(1, (now - start) / dur)
      const ease = 1 - Math.pow(1 - p, 3)
      setV(target * ease)
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [value])
  if (value == null) return <span>—</span>
  return <span>{prefix}{v.toFixed(decimals)}{suffix}</span>
}

const Stat = ({ label, children, color = '#fff', big }) => (
  <div>
    <div style={{ ...mono, fontSize: 9, letterSpacing: '0.2em', color: 'var(--muted)' }}>{label}</div>
    <div style={{ ...mono, fontSize: big ? 26 : 17, fontWeight: 800, color, textShadow: `0 0 16px ${color}33`, marginTop: 2 }}>
      {children}
    </div>
  </div>
)

/* ═══ signal rail row ═══ */
function SignalRow({ s, dir }) {
  const c = dir > 0 ? 'var(--green)' : 'var(--red)'
  const pct = Math.min(Math.abs(s.score ?? 0), 60) / 60 * 100
  return (
    <Link to="/signals" style={{ textDecoration: 'none' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 2px', borderBottom: '1px solid rgba(255,36,71,0.06)', cursor: 'pointer' }}>
        <span style={{ ...mono, fontSize: 12, fontWeight: 800, color: '#fff', minWidth: 74 }}>{s.ticker}</span>
        <div style={{ flex: 1 }} className="bar-track">
          <div className={dir > 0 ? 'bar-fill-bull' : 'bar-fill-bear'} style={{ width: `${pct}%` }} />
        </div>
        <span style={{ ...mono, fontSize: 12, fontWeight: 800, color: c, minWidth: 52, textAlign: 'right' }}>
          {s.score > 0 ? '+' : ''}{Number(s.score).toFixed(1)}
        </span>
      </div>
    </Link>
  )
}

/* ═══ THE DECK ═══ */
export default function Overview() {
  const [summary, setSummary] = useState(null)
  const [brain, setBrain] = useState(null)
  const [alerts, setAlerts] = useState(null)

  const load = useCallback(() => {
    axios.get('/api/summary').then(r => setSummary(r.data)).catch(() => {})
    axios.get('/api/brain/status').then(r => setBrain(r.data)).catch(() => {})
    axios.get('/api/alerts').then(r => setAlerts(r.data)).catch(() => {})
  }, [])
  useEffect(() => { load(); const id = setInterval(load, 20000); return () => clearInterval(id) }, [load])

  const bull = summary?.bullish ?? 0, bear = summary?.bearish ?? 0, neut = summary?.neutral ?? 0
  const total = summary?.assets_total || 1
  const heat = Math.min(1, (bull + bear) / total + (alerts?.critical ? 0.3 : 0))
  const daemonOn = brain?.daemon?.running
  const alertList = (alerts?.alerts || []).slice(0, 5)

  return (
    <div style={{ maxWidth: 1180, margin: '0 auto' }}>

      {/* ═══ HERO — the core and the day's truth ═══ */}
      <div className="glow-frame glow-frame-pulse anim-rise" style={{
        display: 'grid', gridTemplateColumns: '280px 1fr', gap: 0,
        background: 'radial-gradient(600px 300px at 20% 50%, rgba(255,36,71,0.05), transparent), var(--bg-1)',
        overflow: 'hidden', marginBottom: 16,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 18, borderRight: '1px solid var(--border)' }}>
          <ReactorCore intensity={heat} size={230} />
        </div>
        <div style={{ padding: '22px 26px', display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <div style={{ ...mono, fontSize: 10, letterSpacing: '0.3em', color: 'var(--orange)', textShadow: 'var(--glow)' }}>
              ARIA COMMAND ▮ LIVE MARKET STATE
            </div>
            <div style={{ ...mono, fontSize: 30, fontWeight: 800, color: '#fff', marginTop: 6, letterSpacing: '0.02em' }}>
              {summary?.market_regime || '— scanning —'}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 40, flexWrap: 'wrap' }}>
            <Stat label="ASSETS TRACKED" big><Count value={summary?.assets_total} /></Stat>
            <Stat label="BULLISH" color="var(--green)" big><Count value={bull} /></Stat>
            <Stat label="BEARISH" color="var(--red)" big><Count value={bear} /></Stat>
            <Stat label="NEUTRAL" color="var(--yellow)" big><Count value={neut} /></Stat>
            <Stat label="VIX" color="var(--blue)" big><Count value={summary?.vix} decimals={1} /></Stat>
            <Stat label="MACRO SCORE" color="var(--orange)" big><Count value={summary?.macro_score} decimals={1} /></Stat>
          </div>
          {/* breadth bar */}
          <div>
            <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', border: '1px solid var(--border)' }}>
              <div style={{ width: `${(bull / total) * 100}%`, background: 'linear-gradient(90deg, var(--green-dim), var(--green))', transition: 'width 1s' }} />
              <div style={{ width: `${(neut / total) * 100}%`, background: '#3a2a10', transition: 'width 1s' }} />
              <div style={{ width: `${(bear / total) * 100}%`, background: 'linear-gradient(90deg, var(--red), var(--red-dim))', transition: 'width 1s' }} />
            </div>
            <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 4, letterSpacing: '0.1em' }}>
              MARKET BREADTH · {((bull / total) * 100).toFixed(0)}% ADVANCING
            </div>
          </div>
        </div>
      </div>

      {/* ═══ RAILS ═══ */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14, marginBottom: 14 }}>
        <div className="bb-card anim-rise-1">
          <div className="bb-card-header">TOP BULLISH SIGNALS</div>
          {(summary?.top_bullish || []).slice(0, 5).map((s, i) => <SignalRow key={i} s={s} dir={1} />)}
          {!summary?.top_bullish?.length && <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>awaiting engine…</div>}
        </div>
        <div className="bb-card anim-rise-2">
          <div className="bb-card-header">TOP BEARISH SIGNALS</div>
          {(summary?.top_bearish || []).slice(0, 5).map((s, i) => <SignalRow key={i} s={s} dir={-1} />)}
          {!summary?.top_bearish?.length && <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>awaiting engine…</div>}
        </div>
        <div className="bb-card anim-rise-3">
          <div className="bb-card-header">LIVE ALERTS</div>
          {alertList.length === 0 && <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>silence is a feature</div>}
          {alertList.map((a, i) => (
            <div key={i} style={{ padding: '5px 0', borderBottom: '1px solid rgba(255,36,71,0.06)' }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span className={`score-pill ${a.severity === 'CRITICAL' ? 'pill-bear' : a.severity === 'WARNING' ? 'pill-neut' : 'pill-orange'}`} style={{ fontSize: 8 }}>
                  {a.severity}
                </span>
                <span style={{ ...mono, fontSize: 10, color: 'var(--text)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {a.title || a.message}
                </span>
              </div>
            </div>
          ))}
          <Link to="/alerts" style={{ ...mono, fontSize: 9, color: 'var(--orange)', textDecoration: 'none', display: 'block', marginTop: 8, letterSpacing: '0.15em' }}>
            ALL ALERTS →
          </Link>
        </div>
      </div>

      {/* ═══ STRIP — the cognitive engine ═══ */}
      <div style={{ marginBottom: 14 }}>
        {/* brain strip */}
        <Link to="/brain" style={{ textDecoration: 'none' }}>
          <div className="bb-card anim-rise-4" style={{ cursor: 'pointer' }}>
            <div className="bb-card-header">COGNITIVE ENGINE</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
              <span style={{
                ...mono, fontSize: 12, fontWeight: 800,
                color: daemonOn ? 'var(--green)' : 'var(--muted)',
                textShadow: daemonOn ? '0 0 10px rgba(43,227,139,.5)' : 'none',
              }}>
                {daemonOn ? (brain?.daemon?.thinking ? '● THINKING' : '● ALIVE') : '○ ASLEEP'}
              </span>
              <span style={{ ...mono, fontSize: 11, color: 'var(--text-dim)' }}>
                cycle #{brain?.daemon?.cycle_count ?? 0} · {brain?.daemon?.memory_count ?? '—'} memories
              </span>
              <span style={{ ...mono, fontSize: 10, color: 'var(--orange)' }}>
                {brain?.daemon?.model || brain?.models?.[0]?.name || 'local model'}
              </span>
              <span style={{ ...mono, fontSize: 9, color: 'var(--muted)', letterSpacing: '0.15em', marginLeft: 'auto' }}>
                WATCH IT THINK →
              </span>
            </div>
          </div>
        </Link>
      </div>

      {/* ═══ QUICK DECK ═══ */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14 }}>
        {[
          { to: '/map',      icon: '◉', title: 'SIGNAL MAP',  sub: 'the market as a living graph' },
          { to: '/explorer', icon: '⌕', title: 'EXPLORER',    sub: '3,300+ symbols · ₹ £ $ on demand' },
          { to: '/quantlab', icon: '⚗', title: 'QUANT LAB',   sub: 'reads papers · builds · backtests' },
          { to: '/chat',     icon: '◉', title: 'ASK ARIA',    sub: 'your market, interrogated' },
        ].map((c, i) => (
          <Link key={c.to} to={c.to} style={{ textDecoration: 'none' }}>
            <div className={`bb-card anim-rise-${i + 1}`} style={{ cursor: 'pointer', textAlign: 'center', padding: '18px 12px' }}>
              <div style={{ fontSize: 20, color: 'var(--orange)', textShadow: 'var(--glow)' }}>{c.icon}</div>
              <div style={{ ...mono, fontSize: 11, fontWeight: 800, letterSpacing: '0.18em', color: '#fff', marginTop: 8 }}>{c.title}</div>
              <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 4 }}>{c.sub}</div>
            </div>
          </Link>
        ))}
      </div>

      <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', textAlign: 'center', marginTop: 18, letterSpacing: '0.08em' }}>
        ARIA · open-source finance intelligence · research & education only — not financial advice
      </div>
    </div>
  )
}
