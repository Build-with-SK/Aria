import React, { useEffect, useState } from 'react'
import axios from 'axios'
import { useCurrency } from '../currency/CurrencyContext'

const mono = { fontFamily: 'var(--mono)' }
const card = { border: '1px solid var(--border)', background: '#070707', borderRadius: 6, padding: 14, marginBottom: 14 }
const H = ({ children }) => (
  <div style={{ ...mono, fontSize: 11, fontWeight: 800, color: 'var(--orange)', letterSpacing: 2, marginBottom: 10 }}>{children}</div>
)
/* Scenario P&L is book-level and denominated in USD; the hook converts it
   to whatever the viewer is reading in. */
const useMoney = () => {
  const { price } = useCurrency()
  return n => (n >= 0 ? '+' : '−') + price(Math.abs(n), { from: 'USD', digits: 0 }).text
}

// ─── Scenario stress panel ────────────────────────────────────────────────────
function Scenarios() {
  const money = useMoney()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    axios.get('/api/quant/scenarios').then(r => setData(r.data)).catch(() => setData(null)).finally(() => setLoading(false))
  }, [])

  return (
    <div style={card}>
      <H>◆ MACRO STRESS SCENARIOS — portfolio P&L under shocks</H>
      {loading && <div style={{ ...mono, fontSize: 11, color: 'var(--muted)' }}>running scenarios…</div>}
      {data?.scenarios?.map((s, i) => {
        const up = s.pnl_estimate >= 0
        return (
          <div key={i} style={{ borderLeft: `3px solid ${up ? 'var(--green)' : 'var(--red)'}`, padding: '8px 12px', marginBottom: 8, background: '#0a0a0a' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
              <span style={{ ...mono, fontSize: 12, fontWeight: 700, color: '#ddd' }}>{s.name}</span>
              <span style={{ ...mono, fontSize: 13, fontWeight: 800, color: up ? 'var(--green)' : 'var(--red)' }}>
                {money(s.pnl_estimate)} ({s.pnl_pct >= 0 ? '+' : ''}{s.pnl_pct}%)
              </span>
              <span style={{ ...mono, fontSize: 9, color: 'var(--muted)', border: '1px solid #222', padding: '1px 6px', borderRadius: 3 }}>
                {s.confidence}
              </span>
            </div>
            <div style={{ ...mono, fontSize: 10, color: 'var(--text-dim)', marginTop: 3 }}>{s.description}</div>
            <div style={{ display: 'flex', gap: 20, marginTop: 6, flexWrap: 'wrap' }}>
              <div>
                <span style={{ ...mono, fontSize: 9, color: 'var(--red)' }}>LOSERS </span>
                {s.top_losers.slice(0, 3).map(([t, p], j) => (
                  <span key={j} style={{ ...mono, fontSize: 10, color: '#999', marginRight: 8 }}>{t} {money(p)}</span>
                ))}
              </div>
              <div>
                <span style={{ ...mono, fontSize: 9, color: 'var(--green)' }}>WINNERS </span>
                {s.top_winners.slice(0, 3).map(([t, p], j) => (
                  <span key={j} style={{ ...mono, fontSize: 10, color: '#999', marginRight: 8 }}>{t} {money(p)}</span>
                ))}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ─── Options Greeks calculator ────────────────────────────────────────────────
function OptionsCalc() {
  const [ticker, setTicker] = useState('AAPL')
  const [report, setReport] = useState('')
  const [loading, setLoading] = useState(false)

  const run = () => {
    if (!ticker.trim()) return
    setLoading(true)
    axios.get(`/api/quant/options/${ticker.trim().toUpperCase()}?days=30`)
      .then(r => setReport(r.data.report))
      .catch(e => setReport(`⚠ ${e.response?.data?.detail || e.message}`))
      .finally(() => setLoading(false))
  }
  useEffect(() => { run() }, [])   // load AAPL on mount

  return (
    <div style={card}>
      <H>◆ OPTIONS ANALYTICS — Black-Scholes greeks</H>
      <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
        <input value={ticker} onChange={e => setTicker(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') run() }}
          placeholder="ticker (e.g. AAPL, NVDA)"
          style={{ ...mono, flex: 1, background: '#0d0d0d', border: '1px solid #222', color: '#ddd', fontSize: 11, padding: '7px 10px', borderRadius: 3, outline: 'none' }} />
        <button onClick={run} disabled={loading} style={{ ...mono, background: 'transparent', border: '1px solid var(--orange)', color: 'var(--orange)', borderRadius: 3, padding: '6px 14px', fontSize: 11, fontWeight: 800, cursor: 'pointer' }}>
          {loading ? '…' : 'ANALYSE'}
        </button>
      </div>
      <pre style={{ ...mono, fontSize: 10.5, color: '#bbb', lineHeight: 1.55, whiteSpace: 'pre-wrap', background: '#050505', border: '1px solid var(--border)', borderRadius: 4, padding: 10, margin: 0, overflowX: 'auto' }}>
        {report || '—'}
      </pre>
    </div>
  )
}

// ─── Payoff chart (inline SVG) ────────────────────────────────────────────────
function PayoffChart({ curve, spot, breakevens }) {
  if (!curve?.length) return null
  const W = 520, Hh = 180, pad = 28
  const xs = curve.map(c => c[0]), ys = curve.map(c => c[1])
  const xmin = Math.min(...xs), xmax = Math.max(...xs)
  const ymin = Math.min(...ys), ymax = Math.max(...ys)
  const sx = x => pad + (x - xmin) / (xmax - xmin) * (W - 2 * pad)
  const sy = y => Hh - pad - (y - ymin) / (ymax - ymin) * (Hh - 2 * pad)
  const zeroY = sy(0)
  const path = curve.map((c, i) => `${i ? 'L' : 'M'}${sx(c[0]).toFixed(1)},${sy(c[1]).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${Hh}`} style={{ width: '100%', height: 'auto' }}>
      <line x1={pad} y1={zeroY} x2={W - pad} y2={zeroY} stroke="#333" strokeWidth="1" strokeDasharray="3 3" />
      <line x1={sx(spot)} y1={pad} x2={sx(spot)} y2={Hh - pad} stroke="var(--orange)" strokeWidth="1" strokeDasharray="2 2" opacity="0.5" />
      <text x={sx(spot)} y={pad - 4} fill="var(--orange)" fontSize="9" fontFamily="var(--mono)" textAnchor="middle">spot ${spot?.toFixed(0)}</text>
      {(breakevens || []).map((b, i) => (
        <circle key={i} cx={sx(b)} cy={zeroY} r="3" fill="var(--yellow)" />
      ))}
      <path d={path} fill="none" stroke="var(--green)" strokeWidth="2" />
      <text x={pad} y={Hh - 6} fill="#555" fontSize="8" fontFamily="var(--mono)">${xmin.toFixed(0)}</text>
      <text x={W - pad} y={Hh - 6} fill="#555" fontSize="8" fontFamily="var(--mono)" textAnchor="end">${xmax.toFixed(0)}</text>
    </svg>
  )
}

// ─── Strategy analyzer ────────────────────────────────────────────────────────
const STRATS = [
  { id: 'bull_call_spread', label: 'Bull Call Spread' },
  { id: 'bear_put_spread', label: 'Bear Put Spread' },
  { id: 'long_straddle', label: 'Long Straddle' },
  { id: 'iron_condor', label: 'Iron Condor' },
]
function Strategies() {
  const money = useMoney()
  const [ticker, setTicker] = useState('AAPL')
  const [strat, setStrat] = useState('bull_call_spread')
  const [res, setRes] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const run = () => {
    setLoading(true); setErr('')
    axios.get(`/api/quant/strategy/${strat}?ticker=${ticker.trim().toUpperCase()}&days=30`)
      .then(r => setRes(r.data))
      .catch(e => { setErr(e.response?.data?.detail || e.message); setRes(null) })
      .finally(() => setLoading(false))
  }
  useEffect(() => { run() }, [strat])   // re-run when strategy changes

  return (
    <div style={card}>
      <H>◆ DERIVATIVES — multi-leg strategy payoff</H>
      <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap' }}>
        <input value={ticker} onChange={e => setTicker(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') run() }}
          style={{ ...mono, width: 110, background: '#0d0d0d', border: '1px solid #222', color: '#ddd', fontSize: 11, padding: '6px 8px', borderRadius: 3, outline: 'none' }} />
        {STRATS.map(s => (
          <button key={s.id} onClick={() => setStrat(s.id)}
            style={{ ...mono, fontSize: 10, fontWeight: 700, padding: '5px 10px', borderRadius: 3, cursor: 'pointer',
              background: strat === s.id ? 'var(--orange)' : 'transparent', color: strat === s.id ? '#000' : 'var(--text-dim)',
              border: `1px solid ${strat === s.id ? 'var(--orange)' : '#222'}` }}>{s.label}</button>
        ))}
      </div>
      {loading && <div style={{ ...mono, fontSize: 11, color: 'var(--muted)' }}>pricing…</div>}
      {err && <div style={{ ...mono, fontSize: 11, color: 'var(--red)' }}>⚠ {err}</div>}
      {res && !loading && (
        <div>
          <PayoffChart curve={res.payoff_curve} spot={res.spot} breakevens={res.breakevens} />
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginTop: 8 }}>
            <Stat label="Net" v={money(res.net_debit_credit)} c={res.net_debit_credit >= 0 ? 'var(--red)' : 'var(--green)'} sub={res.net_debit_credit >= 0 ? 'debit' : 'credit'} />
            <Stat label="Max Profit" v={money(res.max_profit)} c="var(--green)" />
            <Stat label="Max Loss" v={money(res.max_loss)} c="var(--red)" />
            <Stat label="Breakeven" v={res.breakevens.map(b => money(b)).join(' / ') || '—'} c="var(--yellow)" />
            <Stat label="Net Δ" v={res.net_greeks.delta.toFixed(1)} c="#4da6ff" />
            <Stat label="Net Θ/day" v={money(res.net_greeks.theta)} c="#4da6ff" />
          </div>
          <div style={{ marginTop: 8 }}>
            {res.notes.map((n, i) => (
              <div key={i} style={{ ...mono, fontSize: 10, color: 'var(--text-dim)', lineHeight: 1.6 }}>▸ {n}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
const Stat = ({ label, v, c, sub }) => (
  <div>
    <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', letterSpacing: 1 }}>{label}</div>
    <div style={{ ...mono, fontSize: 13, fontWeight: 800, color: c }}>{v} {sub && <span style={{ fontSize: 8, color: 'var(--muted)' }}>{sub}</span>}</div>
  </div>
)

// ─── Page ─────────────────────────────────────────────────────────────────────
export default function Quant() {
  return (
    <div style={{ maxWidth: 1000, margin: '0 auto' }}>
      <div style={{ marginBottom: 16 }}>
        <div style={{ ...mono, fontSize: 16, fontWeight: 800, color: 'var(--orange)' }}>STRESS &amp; SCENARIOS</div>
        <div style={{ ...mono, fontSize: 10, color: 'var(--text-dim)' }}>
          What the book loses under shocks · options pricing and greeks · multi-leg payoffs — all computed locally
        </div>
      </div>
      <Scenarios />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 0 }}>
        <Strategies />
        <OptionsCalc />
      </div>
      <div style={{ ...mono, fontSize: 9, color: '#2a2a2a', textAlign: 'center', marginTop: 4 }}>
        Theoretical models for research only — not trade recommendations. Actual market prices and risks will differ.
      </div>
    </div>
  )
}
