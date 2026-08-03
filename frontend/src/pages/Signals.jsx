/**
 * pages/Signals.jsx
 * Full signal table with asset detail panel.
 */

import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { RadarChart, PolarGrid, PolarAngleAxis, Radar, ResponsiveContainer } from 'recharts'
import { useSignals } from '../hooks/useApi'
import { Price, useCurrency, useResolveCurrencies } from '../currency/CurrencyContext'
import { Spinner, ErrorBox, ScoreBadge, ActionBadge, ScoreBar, SectionHeader, MetricCard } from '../components/UI'

export default function Signals() {
  const { data, loading, error } = useSignals()
  const { price } = useCurrency()
  const [selected, setSelected]  = useState(null)
  const [filter,   setFilter]    = useState('all')
  const [search,   setSearch]    = useState('')

  if (loading) return <Spinner />
  if (error)   return <ErrorBox message={error} />

  const all = Object.values(data?.signals || {})
  const filtered = all.filter(s => {
    const matchFilter =
      filter === 'all'     ? true :
      filter === 'bullish' ? s.composite_score > 10 :
      filter === 'bearish' ? s.composite_score < -10 :
      filter === 'neutral' ? Math.abs(s.composite_score) <= 10 : true
    const matchSearch = !search || s.ticker.toLowerCase().includes(search.toLowerCase()) || (s.name || '').toLowerCase().includes(search.toLowerCase())
    return matchFilter && matchSearch
  }).sort((a, b) => b.composite_score - a.composite_score)

  const sel = selected ? (data?.signals || {})[selected] : null

  const radarData = sel ? [
    { subject: 'Trend',     value: (sel.trend_score     + 100) / 2 },
    { subject: 'Momentum',  value: (sel.momentum_score  + 100) / 2 },
    { subject: 'Volatility',value: (sel.volatility_score+ 100) / 2 },
    { subject: 'Regime',    value: (sel.regime_score    + 100) / 2 },
    { subject: 'Macro',     value: (sel.macro_score     + 100) / 2 },
    { subject: 'Sentiment', value: (sel.sentiment_score + 100) / 2 },
  ] : []

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>Signal Table</h1>
        <div style={{ display: 'flex', gap: 10 }}>
          <input
            placeholder="Search ticker..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 6, padding: '6px 12px', color: '#e6edf3', fontSize: 13 }}
          />
          {['all','bullish','bearish','neutral'].map(f => (
            <button key={f} onClick={() => setFilter(f)}
              style={{ padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: 'none',
                background: filter === f ? '#58a6ff' : '#21262d', color: filter === f ? '#fff' : '#8b949e' }}>
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: sel ? '1.4fr 1fr' : '1fr', gap: 20 }}>
        {/* Table */}
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr><th>Ticker</th><th>Score</th><th>Signal</th><th>Regime</th><th>Vol</th><th>Risk</th></tr>
            </thead>
            <tbody>
              {filtered.map(s => (
                <tr key={s.ticker} onClick={() => setSelected(s.ticker === selected ? null : s.ticker)}
                  style={{ cursor: 'pointer', background: selected === s.ticker ? 'rgba(88,166,255,0.08)' : '' }}>
                  <td>
                    {/* every ticker in ARIA opens its research dossier */}
                    <Link to={`/research?symbol=${encodeURIComponent(s.ticker)}`}
                      onClick={e => e.stopPropagation()}
                      title={`Open ${s.ticker} research`}
                      style={{ fontWeight: 600, color: 'var(--orange)', textDecoration: 'none' }}>
                      {s.ticker}
                    </Link>
                    <div style={{ fontSize: 11, color: '#8b949e' }}>{s.asset_class}</div>
                  </td>
                  <td><ScoreBadge score={s.composite_score} /></td>
                  <td><ActionBadge action={s.action} /></td>
                  <td style={{ color: '#8b949e', fontSize: 12 }}>{s.regime}</td>
                  <td style={{ color: '#8b949e' }}>{(s.realised_vol * 100).toFixed(0)}%</td>
                  <td style={{ color: s.risk_level === 'Very High' ? '#f85149' : s.risk_level === 'High' ? '#d29922' : '#8b949e', fontSize: 12 }}>{s.risk_level}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Detail panel */}
        {sel && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div className="card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14 }}>
                <div>
                  <div style={{ fontSize: 20, fontWeight: 700 }}>{sel.ticker}</div>
                  <div style={{ fontSize: 12, color: '#8b949e' }}>{sel.name} · {sel.asset_class}</div>
                </div>
                <ActionBadge action={sel.action} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 14 }}>
                <MetricCard label="Price"   value={price(sel.current_price, { symbol: sel.ticker }).text} />
                <MetricCard label="Score"   value={`${sel.composite_score > 0 ? '+' : ''}${sel.composite_score}`} />
                <MetricCard label="Bull Prob" value={`${(sel.bullish_prob * 100).toFixed(0)}%`} color="bull" />
                <MetricCard label="Bear Prob" value={`${(sel.bearish_prob * 100).toFixed(0)}%`} color="bear" />
              </div>
              <SectionHeader>Sub-scores</SectionHeader>
              {[['Trend', sel.trend_score],['Momentum', sel.momentum_score],['Volatility', sel.volatility_score],['Regime', sel.regime_score]].map(([label, val]) => (
                <div key={label} style={{ marginBottom: 8 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                    <span style={{ fontSize: 12, color: '#8b949e' }}>{label}</span>
                    <span style={{ fontSize: 12, fontWeight: 600 }}>{val > 0 ? '+' : ''}{val?.toFixed(1)}</span>
                  </div>
                  <ScoreBar score={val} />
                </div>
              ))}
            </div>

            {/* Radar */}
            <div className="card">
              <SectionHeader>Signal Composition</SectionHeader>
              <ResponsiveContainer width="100%" height={200}>
                <RadarChart data={radarData}>
                  <PolarGrid stroke="#30363d" />
                  <PolarAngleAxis dataKey="subject" tick={{ fill: '#8b949e', fontSize: 11 }} />
                  <Radar dataKey="value" stroke="#58a6ff" fill="#58a6ff" fillOpacity={0.2} />
                </RadarChart>
              </ResponsiveContainer>
            </div>

            {/* Risk params */}
            <div className="card">
              <SectionHeader>Risk Parameters</SectionHeader>
              {[
                ['Stop-Loss',   price(sel.stop_loss, { symbol: sel.ticker }).text],
                ['Take-Profit', price(sel.take_profit, { symbol: sel.ticker }).text],
                ['Position Size', `${sel.position_size_pct?.toFixed(1)}%`],
                ['ATR %',       `${(sel.atr_pct * 100).toFixed(2)}%`],
                ['Risk Level',  sel.risk_level],
              ].map(([label, val]) => (
                <div key={label} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                  <span style={{ fontSize: 12, color: '#8b949e' }}>{label}</span>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>{val}</span>
                </div>
              ))}
            </div>

            {/* Drivers */}
            {sel.drivers?.length > 0 && (
              <div className="card">
                <SectionHeader>Key Drivers</SectionHeader>
                {sel.drivers.filter(d => !d.includes('[Phase')).slice(0, 5).map((d, i) => (
                  <div key={i} style={{ fontSize: 12, color: '#e6edf3', marginBottom: 6 }}>✅ {d}</div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
