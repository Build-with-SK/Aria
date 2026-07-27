/**
 * pages/Recommendations.jsx
 * Investing.com-style technical recommendations: a live scan of the strongest
 * setups, a per-stock multi-timeframe summary, and the honest forward hit-rate.
 */
import React, { useEffect, useState } from 'react'

const MONO = 'var(--mono)'

const LABEL_COLOR = {
  'Strong Buy': 'var(--green)', 'Buy': '#7fd18b',
  'Neutral': 'var(--muted)',
  'Sell': '#e88', 'Strong Sell': 'var(--red)',
}
const badge = (label) => (
  <span style={{
    fontFamily: MONO, fontSize: 10, fontWeight: 800, letterSpacing: '.05em',
    padding: '2px 8px', borderRadius: 4, whiteSpace: 'nowrap',
    color: LABEL_COLOR[label] || 'var(--muted)',
    border: `1px solid ${LABEL_COLOR[label] || 'var(--muted)'}`,
    background: 'rgba(255,255,255,.02)',
  }}>{label || '—'}</span>
)

function Meter({ counts }) {
  if (!counts) return null
  const { buy = 0, sell = 0, neutral = 0 } = counts
  const total = buy + sell + neutral || 1
  const seg = (n, c) => n ? <div style={{ width: `${n / total * 100}%`, background: c, height: 6 }} /> : null
  return (
    <div style={{ display: 'flex', width: 90, height: 6, borderRadius: 3, overflow: 'hidden', border: '1px solid var(--border)' }}>
      {seg(buy, 'var(--green)')}{seg(neutral, 'var(--muted)')}{seg(sell, 'var(--red)')}
    </div>
  )
}

/* ── the strongest-setups scan ── */
function ScanTable({ scan, loading }) {
  const rows = scan
    ? [...(scan.strong_buy || []), ...(scan.buy || []), ...(scan.sell || [])]
    : []
  return (
    <div className="bb-card">
      <div className="bb-card-header">TECHNICAL RECOMMENDATIONS — STRONGEST SETUPS (DAILY)</div>
      {loading && <div style={{ color: 'var(--muted)', fontFamily: MONO, fontSize: 11, padding: 10 }}>scanning the universe…</div>}
      {!loading && !rows.length && <div style={{ color: 'var(--muted)', fontFamily: MONO, fontSize: 11, padding: 10 }}>No strong setups right now.</div>}
      {!!rows.length && (
        <table>
          <thead><tr><th>SYMBOL</th><th>SIGNAL</th><th>BUY / NEU / SELL</th><th>PRICE</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td className="white" style={{ fontWeight: 700 }}>{r.symbol}</td>
                <td>{badge(r.summary)}</td>
                <td><div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Meter counts={r.counts} />
                  <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--muted)' }}>
                    {r.counts?.buy}/{r.counts?.neutral}/{r.counts?.sell}
                  </span>
                </div></td>
                <td style={{ fontFamily: MONO }}>{r.price != null ? Number(r.price).toFixed(2) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {scan && <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--muted)', padding: '6px 2px' }}>
        scanned {scan.scanned} names · updates on refresh</div>}
    </div>
  )
}

/* ── per-stock multi-timeframe summary ── */
function StockLookup() {
  const [sym, setSym] = useState('')
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const go = async () => {
    if (!sym.trim()) return
    setBusy(true); setData(null)
    try {
      const r = await fetch(`/api/technical/summary?symbol=${encodeURIComponent(sym.trim())}&timeframes=5m,15m,1h,1d,1wk`)
      setData(await r.json())
    } catch { setData({ error: 'lookup failed' }) } finally { setBusy(false) }
  }
  const tfs = data?.timeframes || {}
  return (
    <div className="bb-card">
      <div className="bb-card-header">CHECK ANY STOCK — MULTI-TIMEFRAME SUMMARY</div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
        <input value={sym} onChange={e => setSym(e.target.value.toUpperCase())}
          onKeyDown={e => e.key === 'Enter' && go()}
          placeholder="AAPL · SAIL · TSLA · BTC-USD"
          style={{
            flex: 1, fontFamily: MONO, fontSize: 12, padding: '8px 10px',
            background: '#0a0509', color: '#fff', border: '1px solid var(--border)', borderRadius: 4,
          }} />
        <button onClick={go} disabled={busy} style={{
          fontFamily: MONO, fontSize: 11, fontWeight: 800, padding: '8px 16px', borderRadius: 4,
          cursor: 'pointer', background: 'var(--orange-bg)', color: 'var(--orange)', border: '1px solid var(--orange-dim)',
        }}>{busy ? '…' : 'ANALYZE'}</button>
      </div>
      {data?.error && <div style={{ color: 'var(--red)', fontFamily: MONO, fontSize: 11 }}>{data.error}</div>}
      {data && !data.error && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
            <span className="white" style={{ fontFamily: MONO, fontWeight: 800, fontSize: 14 }}>{data.yahoo}</span>
            <span style={{ fontFamily: MONO, fontSize: 10, color: 'var(--muted)' }}>CONSENSUS</span>
            {badge(data.consensus)}
          </div>
          <table>
            <thead><tr><th>TIMEFRAME</th><th>SIGNAL</th><th>BUY / NEU / SELL</th></tr></thead>
            <tbody>
              {Object.entries(tfs).map(([tf, v]) => (
                <tr key={tf}>
                  <td className="white">{tf}</td>
                  <td>{v.summary ? badge(v.summary) : <span style={{ color: 'var(--muted)', fontSize: 10 }}>{v.error ? 'n/a' : '—'}</span>}</td>
                  <td>{v.counts ? <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Meter counts={v.counts} />
                    <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--muted)' }}>{v.counts.buy}/{v.counts.neutral}/{v.counts.sell}</span>
                  </div> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

/* ── forward hit-rate ── */
function Performance() {
  const [perf, setPerf] = useState(null)
  useEffect(() => {
    fetch('/api/technical/performance').then(r => r.json()).then(setPerf).catch(() => {})
  }, [])
  const by = perf?.by_label
  return (
    <div className="bb-card">
      <div className="bb-card-header">FORWARD HIT-RATE — DO THESE SIGNALS WORK?</div>
      {perf?.note && <div style={{ color: 'var(--muted)', fontFamily: MONO, fontSize: 11, padding: 8 }}>{perf.note}</div>}
      {by && (
        <table>
          <thead><tr><th>SIGNAL</th><th>SAMPLE</th><th>HIT RATE</th><th>AVG RETURN (IN CALL DIR)</th></tr></thead>
          <tbody>
            {['Strong Buy', 'Buy', 'Sell', 'Strong Sell', 'bullish', 'bearish', 'ALL']
              .filter(k => by[k]).map(k => {
                const s = by[k]
                return (
                  <tr key={k}>
                    <td>{k === 'ALL' ? <strong className="white">OVERALL</strong> : badge(k[0].toUpperCase() + k.slice(1))}</td>
                    <td style={{ fontFamily: MONO }}>{s.count}</td>
                    <td className={s.hit_rate >= 0.5 ? 'bull' : 'bear'} style={{ fontFamily: MONO }}>{Math.round(s.hit_rate * 100)}%</td>
                    <td className={s.avg_return_in_call_direction_pct >= 0 ? 'bull' : 'bear'} style={{ fontFamily: MONO }}>
                      {s.avg_return_in_call_direction_pct >= 0 ? '+' : ''}{s.avg_return_in_call_direction_pct}%</td>
                  </tr>
                )
              })}
          </tbody>
        </table>
      )}
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--muted)', padding: '6px 2px', lineHeight: 1.6 }}>
        Recommendations are snapshotted daily (21:30) with entry prices; accuracy accrues as they age.
        A "hit" = the call pointed the right way. Research only — not financial advice.
      </div>
    </div>
  )
}

export default function Recommendations() {
  const [scan, setScan] = useState(null)
  const [loading, setLoading] = useState(true)
  const load = () => {
    setLoading(true)
    fetch('/api/technical/recommendations?limit=30')
      .then(r => r.json()).then(d => { setScan(d); setLoading(false) })
      .catch(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 16 }}>
        <div>
          <div style={{ fontFamily: MONO, fontSize: 20, fontWeight: 800, color: 'var(--orange)', letterSpacing: '.2em', textShadow: 'var(--glow)' }}>
            RECOMMENDATIONS
          </div>
          <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--muted)', letterSpacing: '.2em' }}>
            TECHNICAL CONSENSUS · MOVING AVERAGES + OSCILLATORS · MULTI-TIMEFRAME
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <button onClick={load} disabled={loading} style={{
          fontFamily: MONO, fontSize: 11, fontWeight: 800, letterSpacing: '.1em',
          padding: '9px 18px', borderRadius: 5, cursor: 'pointer',
          background: 'var(--orange-bg)', color: 'var(--orange)', border: '1px solid var(--orange-dim)',
        }}>{loading ? '◌ SCANNING…' : '↻ RESCAN'}</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.4fr) minmax(300px,1fr)', gap: 14 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
          <ScanTable scan={scan} loading={loading} />
          <Performance />
        </div>
        <div style={{ minWidth: 0 }}>
          <StockLookup />
        </div>
      </div>
    </div>
  )
}
