import React, { useState, useEffect, useCallback } from 'react'
import { SectionHeader } from './UI'

// ─── OptionsChain ─────────────────────────────────────────────────────────────
// A chain view whose main job is refusing to let a number look like a price.
//
// `lastPrice` is the memory of somebody's last trade. When there is no bid and
// no ask, it is not what you would pay — and the vendor prints it in the same
// column either way. So every row carries a verdict, the untradeable rows are
// visibly untradeable, and the three different reasons a quote can be missing
// are kept apart: the market is shut, the contract is dead, or the vendor
// returned nothing for the entire chain, which says something about the feed
// and nothing about the market.
//
// ARIA does not trade options. The banner is driven by the payload's own
// `tradeable` flag, so it cannot drift out of sync with the backend.

const mono = { fontFamily: 'var(--font)' }
const dim = { color: 'var(--ink-2)' }

const VERDICT = {
  tradeable: { colour: 'var(--green)', label: 'tradeable' },
  thin: { colour: 'var(--accent)', label: 'thin' },
  dead: { colour: 'var(--red)', label: 'no market' },
  'market closed': { colour: 'var(--ink-2)', label: 'market shut' },
}

const num = (v, digits = 2) =>
  v === null || v === undefined ? '—' : Number(v).toFixed(digits)

function Row({ c }) {
  const v = VERDICT[c.liquidity?.verdict] || VERDICT.dead
  const g = c.greeks || {}
  const untradeable = c.liquidity?.verdict !== 'tradeable'
  return (
    <tr style={{ opacity: untradeable ? 0.62 : 1 }}>
      <td style={{ ...mono, fontWeight: 600 }}>{num(c.strike, 1)}</td>
      <td style={mono}>{num(c.bid)}</td>
      <td style={mono}>{num(c.ask)}</td>
      <td style={{ ...mono, fontWeight: 600 }}>
        {c.mid === null ? <span style={dim}>no mid</span> : num(c.mid)}
      </td>
      {/* `last` is deliberately dimmed when there is no live market: it is a
          historical fact, not an offer, and the styling has to say so. */}
      <td style={{ ...mono, ...(c.mid === null ? dim : {}) }}>
        {num(c.last)}
        {c.mid === null && c.last > 0 && (
          <span style={{ fontSize: 12, marginLeft: 4 }}>stale</span>
        )}
      </td>
      <td style={mono}>{c.open_interest?.toLocaleString() ?? '—'}</td>
      <td style={mono}>{c.implied_vol === null ? '—' : `${(c.implied_vol * 100).toFixed(1)}%`}</td>
      <td style={mono}>{g.delta === undefined ? '—' : num(g.delta, 3)}</td>
      <td style={mono}>{g.theta === undefined ? '—' : num(g.theta, 3)}</td>
      <td style={{ ...mono, fontSize: 12, color: v.colour }}>
        {v.label}
        {(c.liquidity?.why || []).length > 0 && (
          <div style={{ ...dim, fontSize: 12 }}>{c.liquidity.why[0]}</div>
        )}
      </td>
    </tr>
  )
}

function Side({ title, rows }) {
  if (!rows?.length) return null
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ ...mono, fontSize: 13, color: 'var(--accent)', marginBottom: 6 }}>
        {title}
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table>
          <thead>
            <tr>
              <th>Strike</th><th>Bid</th><th>Ask</th><th>Mid</th><th>Last</th>
              <th>OI</th><th>IV</th><th>Δ</th><th>Θ/day</th><th>Market</th>
            </tr>
          </thead>
          <tbody>{rows.map(c => <Row key={c.contract || c.strike} c={c} />)}</tbody>
        </table>
      </div>
    </div>
  )
}

export default function OptionsChain() {
  const [symbol, setSymbol] = useState('AAPL')
  const [input, setInput] = useState('AAPL')
  const [expiry, setExpiry] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async (sym, exp) => {
    setLoading(true); setErr('')
    try {
      const q = new URLSearchParams({ symbol: sym, around: '8' })
      if (exp) q.set('expiry', exp)
      const res = await fetch(`/api/options/chain?${q}`)
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail || `chain failed (${res.status})`)
      setData(d)
      if (d.error) setErr(d.error)
    } catch (e) {
      setErr(String(e.message || e)); setData(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load(symbol, expiry) }, [symbol, expiry, load])

  const submit = e => {
    e.preventDefault()
    const s = input.trim().toUpperCase()
    if (s) { setExpiry(''); setSymbol(s) }
  }

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <SectionHeader>Options chain</SectionHeader>

      <form onSubmit={submit} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="symbol"
          style={{ ...mono, background: 'var(--inset)', border: '1px solid var(--line)', color: 'var(--ink)',
                   fontSize: 14, padding: '7px 10px', borderRadius: 10, width: 110, outline: 'none' }}
        />
        <button type="submit" style={{ ...mono, background: 'var(--accent)', color: 'var(--on-solid)', border: 'none',
                 borderRadius: 10, padding: '7px 14px', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>
          Load
        </button>
        {(data?.expiries || []).length > 0 && (
          <select
            value={data.expiry || ''}
            onChange={e => setExpiry(e.target.value)}
            style={{ ...mono, background: 'var(--inset)', color: 'var(--ink-2)', border: '1px solid var(--line)',
                     borderRadius: 10, fontSize: 13, padding: '6px 8px', outline: 'none', cursor: 'pointer' }}
          >
            {data.expiries.map(x => <option key={x} value={x}>{x}</option>)}
          </select>
        )}
        {data?.spot && (
          <span style={{ ...mono, fontSize: 13, ...dim }}>
            spot {num(data.spot)} · {data.days_to_expiry}d to expiry
          </span>
        )}
      </form>

      {/* Driven by the payload, so it cannot drift from what the backend allows. */}
      {data?.tradeable === false && (
        <div style={{ ...mono, fontSize: 13, color: 'var(--accent)', border: '1px solid var(--accent)',
                      borderRadius: 10, padding: '8px 10px', marginBottom: 12, lineHeight: 1.5 }}>
          {data.refusal}
        </div>
      )}

      {loading && <div style={{ ...mono, fontSize: 14, ...dim }}>loading…</div>}
      {err && <div style={{ ...mono, fontSize: 13, color: 'var(--red)', marginBottom: 10 }}>{err}</div>}

      {data?.note && (
        <div style={{ ...mono, fontSize: 13, ...dim, marginBottom: 12, lineHeight: 1.55 }}>
          {data.note}
        </div>
      )}

      <Side title={`Calls · ${data?.expiry || ''}`} rows={data?.calls} />
      <Side title={`Puts · ${data?.expiry || ''}`} rows={data?.puts} />

      {data?.assumptions && (
        <div style={{ ...mono, fontSize: 12, ...dim, borderTop: '1px solid var(--line)', paddingTop: 10, lineHeight: 1.6 }}>
          {data.assumptions.greeks}
          <br />
          Risk-free {(data.assumptions.risk_free_rate * 100).toFixed(2)}% — {data.assumptions.risk_free_source}.
          IV is {data.assumptions.implied_vol}.
        </div>
      )}
    </div>
  )
}
