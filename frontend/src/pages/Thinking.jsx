import React, { useEffect, useRef, useState, useCallback } from 'react'
import axios from 'axios'

const mono = { fontFamily: 'var(--mono)' }

// phase → colour + glyph
const PHASE = {
  ORIENT:  { c: '#22d3ee', g: '◈', label: 'ORIENT'  },
  FOCUS:   { c: '#a78bfa', g: '◎', label: 'FOCUS'   },
  RECALL:  { c: '#f472b6', g: '❖', label: 'RECALL'  },
  ANALYSE: { c: '#60a5fa', g: '⊛', label: 'ANALYSE' },
  DECIDE:  { c: '#34d399', g: '▲', label: 'DECIDE'  },
  REFLECT: { c: '#fbbf24', g: '◐', label: 'REFLECT' },
}
const phaseOf = (step) => {
  const base = (step || '').split('_')[0].toUpperCase()
  return PHASE[base] || { c: '#7dd3fc', g: '·', label: step }
}

// ─── The animated neural core ─────────────────────────────────────────────────
function Core({ thinking, hue }) {
  const ref = useRef(null)
  const state = useRef({ hue: '#22d3ee', intensity: 0.4 })
  state.current.hue = hue
  state.current.intensity = thinking ? 1 : 0.4

  useEffect(() => {
    const cv = ref.current, cx = cv.getContext('2d')
    const CX = 130, CY = 130
    const P = Array.from({ length: 60 }, () => ({
      a: Math.random() * 6.28, r: 34 + Math.random() * 70,
      s: 0.003 + Math.random() * 0.012, z: Math.random(),
    }))
    const hexA = (h, a) => { const n = parseInt(h.slice(1), 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})` }
    let raf, t0 = performance.now()
    const draw = (now) => {
      const t = (now - t0) / 1000
      const { hue, intensity } = state.current
      cx.clearRect(0, 0, 260, 260)
      const breath = 1 + 0.05 * Math.sin(t * 2.2 * intensity)
      for (let i = 0; i < 5; i++) { const r = (26 + i * 17) * breath
        cx.beginPath(); cx.arc(CX, CY, r, 0, 6.29)
        cx.strokeStyle = hexA(hue, 0.26 - i * 0.04); cx.lineWidth = i === 0 ? 2 : 1; cx.stroke() }
      for (let i = 0; i < 3; i++) { const r = (36 + i * 17) * breath, off = t * (0.8 + i * 0.5) * intensity * (i % 2 ? -1 : 1)
        cx.beginPath(); cx.arc(CX, CY, r, off, off + 1.3 + i * 0.5)
        cx.strokeStyle = hexA(hue, 0.85); cx.lineWidth = 2.4; cx.lineCap = 'round'; cx.stroke() }
      for (const p of P) { p.a += p.s * (0.4 + intensity); const x = CX + Math.cos(p.a) * p.r * breath, y = CY + Math.sin(p.a) * p.r * breath
        cx.beginPath(); cx.arc(x, y, 0.8 + p.z * 1.5, 0, 6.29); cx.fillStyle = hexA(hue, 0.18 + 0.5 * p.z); cx.fill() }
      const g = cx.createRadialGradient(CX, CY, 1, CX, CY, 38 * breath)
      g.addColorStop(0, hexA('#dffbff', 0.92)); g.addColorStop(0.4, hexA(hue, 0.5)); g.addColorStop(1, 'rgba(0,0,0,0)')
      cx.beginPath(); cx.arc(CX, CY, 38 * breath, 0, 6.29); cx.fillStyle = g; cx.fill()
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [])
  return <canvas ref={ref} width={260} height={260} style={{ width: 260, height: 260 }} />
}

// ─── One streamed thought row (typewriter) ────────────────────────────────────
function Thought({ step, thought, conclusion, active, onDone }) {
  const p = phaseOf(step)
  const [shown, setShown] = useState(active ? '' : thought)
  useEffect(() => {
    if (!active) { setShown(thought); return }
    let i = 0, alive = true
    const tick = () => {
      if (!alive) return
      if (i <= thought.length) { setShown(thought.slice(0, i)); i += 3; setTimeout(tick, 10) }
      else onDone && onDone()
    }
    tick()
    return () => { alive = false }
  }, [active, thought])
  return (
    <div style={{ marginBottom: 14, paddingLeft: 14, borderLeft: `2px solid ${p.c}`,
      animation: 'thoughtIn .4s ease' }}>
      <div style={{ ...mono, fontSize: 11, fontWeight: 800, letterSpacing: 1, color: p.c }}>
        {p.g} {step.replace('_', ' ')}
      </div>
      <div style={{ ...mono, fontSize: 12, lineHeight: 1.6, color: '#b9d4ea', marginTop: 3, whiteSpace: 'pre-wrap' }}>
        {shown}{active && shown.length < thought.length && <span style={{ color: p.c }}>▊</span>}
      </div>
      {conclusion && !active && (
        <div style={{ ...mono, fontSize: 11, color: p.c, marginTop: 4 }}>→ {conclusion}</div>
      )}
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────
export default function Thinking() {
  const [cycle, setCycle] = useState(null)
  const [status, setStatus] = useState(null)
  const [steps, setSteps] = useState([])      // [{step,thought,conclusion}]
  const [activeIdx, setActiveIdx] = useState(-1)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const lastCycleId = useRef(null)
  const pollRef = useRef(null)

  const activePhase = activeIdx >= 0 && steps[activeIdx] ? phaseOf(steps[activeIdx].step) : null
  const thinking = status?.daemon?.thinking || activeIdx >= 0
  const hue = activePhase ? activePhase.c : (thinking ? '#22d3ee' : '#22d3ee')

  // stream a set of steps one-by-one
  const streamSteps = useCallback((ts) => {
    setSteps(ts); setActiveIdx(ts.length ? 0 : -1)
  }, [])

  const loadCycle = useCallback((animate) => {
    axios.get('/api/brain/last-cycle').then(r => {
      const d = r.data
      setCycle(d)
      const ts = d.thinking_steps || []
      if (d.cycle_id && d.cycle_id !== lastCycleId.current) {
        lastCycleId.current = d.cycle_id
        if (animate) streamSteps(ts)
        else { setSteps(ts); setActiveIdx(-1) }
      }
    }).catch(() => {})
  }, [streamSteps])

  useEffect(() => {
    loadCycle(false)
    const poll = () => axios.get('/api/brain/status').then(r => setStatus(r.data)).catch(() => {})
    poll(); const id = setInterval(poll, 4000)
    return () => clearInterval(id)
  }, [loadCycle])

  // advance the typewriter chain
  const onThoughtDone = () => {
    setActiveIdx(i => (i + 1 < steps.length ? i + 1 : -1))
  }

  const runNow = async () => {
    setBusy(true); setNote('waking the brain…')
    try {
      await axios.post('/api/brain/run-now')
      setNote('thinking… (a full cycle takes a minute or two on the local model)')
      // poll for the new cycle
      clearInterval(pollRef.current)
      pollRef.current = setInterval(async () => {
        const s = await axios.get('/api/brain/status').then(r => r.data).catch(() => null)
        setStatus(s)
        const d = await axios.get('/api/brain/last-cycle').then(r => r.data).catch(() => null)
        if (d && d.cycle_id && d.cycle_id !== lastCycleId.current && (!s?.daemon?.thinking)) {
          lastCycleId.current = d.cycle_id
          setCycle(d); streamSteps(d.thinking_steps || [])
          setNote('cycle complete'); clearInterval(pollRef.current); setBusy(false)
        }
      }, 3000)
    } catch (e) {
      setNote(`⚠ ${e.response?.data?.detail || e.message}`); setBusy(false)
    }
  }
  useEffect(() => () => clearInterval(pollRef.current), [])

  const decisions = cycle?.decisions || []
  const ollamaUp = status?.ollama_running

  return (
    <div style={{ maxWidth: 1040, margin: '0 auto' }}>
      <style>{`@keyframes thoughtIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}`}</style>

      {/* header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
        <div style={{ ...mono, fontSize: 17, fontWeight: 800, color: 'var(--orange)', letterSpacing: 1 }}>
          ARIA · LIVE MIND
        </div>
        <span style={{ ...mono, fontSize: 11, fontWeight: 800,
          color: thinking ? 'var(--green)' : 'var(--muted)',
          animation: thinking ? 'pulse 1.4s infinite' : 'none' }}>
          {thinking ? '● THINKING' : '○ IDLE'}
        </span>
        <span style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>cycle #{cycle?.cycle_count ?? 0}</span>
        <span style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>model {cycle?.model || status?.daemon?.model || '—'}</span>
        <div style={{ marginLeft: 'auto' }}>
          <button onClick={runNow} disabled={busy || !ollamaUp} style={{
            ...mono, background: busy || !ollamaUp ? '#1a1a1a' : 'var(--orange)',
            color: busy || !ollamaUp ? '#555' : '#000', border: 'none', borderRadius: 5,
            padding: '8px 18px', fontSize: 12, fontWeight: 800, letterSpacing: 1,
            cursor: busy || !ollamaUp ? 'not-allowed' : 'pointer' }}>
            {busy ? 'THINKING…' : '▶ THINK NOW'}
          </button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 0,
        border: '1px solid var(--border)', background: '#05070d', borderRadius: 10, overflow: 'hidden', minHeight: 520 }}>

        {/* core column */}
        <div style={{ borderRight: '1px solid #12203a', display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center', padding: 20 }}>
          <Core thinking={thinking} hue={hue} />
          <div style={{ ...mono, fontSize: 12, fontWeight: 800, letterSpacing: 2, marginTop: 10,
            color: activePhase ? activePhase.c : '#22d3ee' }}>
            {activePhase ? activePhase.label : (thinking ? 'REASONING' : 'IDLE')}
          </div>
          <div style={{ ...mono, fontSize: 9, color: '#3a5a7a', marginTop: 6, textAlign: 'center', lineHeight: 1.6 }}>
            regime {cycle?.regime || '—'}<br />VIX {cycle?.vix ?? '—'} · {steps.length} steps · {cycle?.memory_count ?? 0} memories
          </div>
          {note && <div style={{ ...mono, fontSize: 9, color: 'var(--yellow)', marginTop: 10, textAlign: 'center' }}>{note}</div>}
          {!ollamaUp && <div style={{ ...mono, fontSize: 9, color: 'var(--red)', marginTop: 8 }}>Ollama offline</div>}
        </div>

        {/* thought stream */}
        <div style={{ padding: '18px 20px', overflowY: 'auto', maxHeight: 560 }}>
          {steps.length === 0 ? (
            <div style={{ ...mono, fontSize: 12, color: '#3a5a7a', textAlign: 'center', paddingTop: 60 }}>
              No reasoning cycle yet. Press <span style={{ color: 'var(--orange)' }}>THINK NOW</span> to watch ARIA reason
              through the market step by step.
            </div>
          ) : steps.map((s, i) => (
            <Thought key={`${cycle?.cycle_id}-${i}`} step={s.step} thought={s.thought}
              conclusion={s.conclusion} active={i === activeIdx} onDone={onThoughtDone} />
          ))}

          {/* decisions */}
          {activeIdx === -1 && decisions.length > 0 && (
            <div style={{ marginTop: 10, borderTop: '1px solid #12203a', paddingTop: 12 }}>
              <div style={{ ...mono, fontSize: 11, fontWeight: 800, color: 'var(--green)', letterSpacing: 1, marginBottom: 8 }}>
                ▲ DECISIONS · {cycle?.trades_queued || 0} queued for approval
              </div>
              {decisions.map((d, i) => (
                <div key={i} style={{ ...mono, fontSize: 12, lineHeight: 1.8, color: '#b9d4ea' }}>
                  <span style={{ color: '#fff', fontWeight: 700 }}>{d.ticker}</span>{' · '}
                  <span style={{ color: d.action === 'PROPOSE_BUY' ? 'var(--green)' : d.action === 'PROPOSE_SELL' ? 'var(--red)' : 'var(--muted)', fontWeight: 700 }}>{d.action}</span>
                  {' · '}<span style={{ color: 'var(--yellow)' }}>{d.conviction}</span>{' · '}
                  <span style={{ color: '#8fb0cc' }}>{d.reason}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <div style={{ ...mono, fontSize: 9, color: '#2a3a4a', textAlign: 'center', marginTop: 6 }}>
        Live chain-of-thought from ARIA's cognitive engine · the brain proposes, the human approves · research only
      </div>
    </div>
  )
}
