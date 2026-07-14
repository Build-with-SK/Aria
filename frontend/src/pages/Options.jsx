import React, { useState } from 'react'
import { useOptions } from '../hooks/useApi'
import { Spinner, ErrorBox, ScoreBadge, SectionHeader, MetricCard } from '../components/UI'

export default function Options() {
  const { data, loading, error } = useOptions()
  const [selected, setSelected] = useState(null)
  if (loading) return <Spinner />
  if (error)   return <ErrorBox message={error} />
  const valid = Object.entries(data || {}).filter(([,o]) => o && !o.error)
  const sel   = selected ? (data||{})[selected] : null
  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20 }}>Options Analysis</h1>
      <div style={{ display: 'grid', gridTemplateColumns: sel ? '1.2fr 1fr' : '1fr', gap: 20 }}>
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>Ticker</th><th>Expiry</th><th>DTE</th><th>P/C OI</th><th>IV Skew</th><th>Sentiment</th></tr></thead>
            <tbody>
              {valid.sort((a,b) => (b[1].final_sentiment_score||0)-(a[1].final_sentiment_score||0)).map(([ticker, o]) => (
                <tr key={ticker} onClick={() => setSelected(ticker === selected ? null : ticker)}
                  style={{ cursor: 'pointer', background: selected === ticker ? 'rgba(88,166,255,0.08)' : '' }}>
                  <td style={{ fontWeight: 600 }}>{ticker}</td>
                  <td style={{ fontSize: 12, color: '#8b949e' }}>{o.selected_expiry}</td>
                  <td style={{ color: '#8b949e' }}>{o.days_to_expiry}d</td>
                  <td style={{ color: o.pc_oi_ratio > 1.1 ? '#f85149' : o.pc_oi_ratio < 0.9 ? '#3fb950' : '#d29922' }}>{o.pc_oi_ratio?.toFixed(2)}</td>
                  <td style={{ color: o.iv_skew > 0.02 ? '#f85149' : '#3fb950' }}>{o.iv_skew?.toFixed(3)}</td>
                  <td><ScoreBadge score={o.final_sentiment_score} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {sel && (
          <div className="card">
            <SectionHeader>{selected} — Options Detail</SectionHeader>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 14 }}>
              <MetricCard label="Spot Price" value={`$${parseFloat(sel.underlying_price).toLocaleString(undefined,{minimumFractionDigits:2})}`} />
              <MetricCard label="ATM Strike" value={`$${parseFloat(sel.atm_strike).toLocaleString(undefined,{minimumFractionDigits:2})}`} />
              <MetricCard label="ATM Call"   value={`$${sel.atm_call_price?.toFixed(2)}`} color="bull" />
              <MetricCard label="ATM Put"    value={`$${sel.atm_put_price?.toFixed(2)}`}  color="bear" />
              <MetricCard label="P/C OI"     value={sel.pc_oi_ratio?.toFixed(3)} color={sel.pc_oi_ratio > 1.1 ? 'bear' : 'bull'} />
              <MetricCard label="IV Skew"    value={sel.iv_skew?.toFixed(4)} color={sel.iv_skew > 0.02 ? 'bear' : 'bull'} />
            </div>
            {[['Call Breakeven', `$${sel.call_breakeven?.toFixed(2)}`],
              ['Put Breakeven',  `$${sel.put_breakeven?.toFixed(2)}`],
              ['Max Call OI',    `$${sel.top_call_strike?.toFixed(2)}`],
              ['Max Put OI',     `$${sel.top_put_strike?.toFixed(2)}`],
              ['Call IV',        `${(sel.avg_call_iv*100)?.toFixed(1)}%`],
              ['Put IV',         `${(sel.avg_put_iv*100)?.toFixed(1)}%`],
            ].map(([l,v]) => (
              <div key={l} style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 0', borderBottom: '1px solid #21262d' }}>
                <span style={{ color: '#8b949e', fontSize: 12 }}>{l}</span>
                <span style={{ fontWeight: 600 }}>{v}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
