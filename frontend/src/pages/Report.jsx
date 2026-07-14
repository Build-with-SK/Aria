import React from 'react'
import { useReport } from '../hooks/useApi'
import { Spinner, ErrorBox, SectionHeader, MetricCard } from '../components/UI'

export default function Report() {
  const { data, loading, error } = useReport()
  if (loading) return <Spinner />
  if (error)   return <ErrorBox message={error} />
  const d = data || {}
  const rs = d.regime_stats || {}
  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Daily Market Intelligence Report</h1>
      <div style={{ color: '#8b949e', fontSize: 13, marginBottom: 20 }}>Date: {d.date} · Regime: <span style={{ color: '#58a6ff', fontWeight: 600 }}>{d.market_regime}</span></div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginBottom: 20 }}>
        <MetricCard label="Bullish Assets" value={rs.bull} color="bull" />
        <MetricCard label="Neutral"        value={rs.neutral} />
        <MetricCard label="Bearish Assets" value={rs.bear} color="bear" />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 20 }}>
        <div className="card">
          <SectionHeader>🟢 Top Bullish</SectionHeader>
          {(d.top_bullish||[]).map((s,i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid #21262d' }}>
              <span style={{ fontWeight: 600 }}>{s.ticker}</span>
              <span style={{ color: '#8b949e', fontSize: 12 }}>{s.action}</span>
              <span style={{ color: '#3fb950', fontWeight: 700 }}>{s.score > 0 ? '+' : ''}{s.score?.toFixed(1)}</span>
            </div>
          ))}
        </div>
        <div className="card">
          <SectionHeader>🔴 Top Bearish</SectionHeader>
          {(d.top_bearish||[]).map((s,i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid #21262d' }}>
              <span style={{ fontWeight: 600 }}>{s.ticker}</span>
              <span style={{ color: '#8b949e', fontSize: 12 }}>{s.action}</span>
              <span style={{ color: '#f85149', fontWeight: 700 }}>{s.score?.toFixed(1)}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="card">
        <SectionHeader>Summary</SectionHeader>
        {d.futures_summary && <div style={{ marginBottom: 8, fontSize: 13 }}>📡 <b>Futures:</b> {d.futures_summary.n_contracts} contracts · Avg confirmation: <b>{d.futures_summary.avg_confirmation > 0 ? '+' : ''}{d.futures_summary.avg_confirmation}</b></div>}
        {d.options_summary && <div style={{ marginBottom: 8, fontSize: 13 }}>🎯 <b>Options:</b> {d.options_summary.n_analyzed} chains · Avg sentiment: <b>{d.options_summary.avg_sentiment > 0 ? '+' : ''}{d.options_summary.avg_sentiment}</b></div>}
        {d.portfolio_summary && <div style={{ marginBottom: 8, fontSize: 13 }}>🏦 <b>Portfolio:</b> {d.portfolio_summary.total_allocated?.toFixed(1)}% allocated · Div: {d.portfolio_summary.diversification_score?.toFixed(0)}/100</div>}
        {d.macro && <div style={{ fontSize: 13 }}>🌐 <b>Macro:</b> {d.macro.regime} · Score: {d.macro.macro_score > 0 ? '+' : ''}{d.macro.macro_score?.toFixed(1)} · VIX: {d.macro.vix}</div>}
      </div>
    </div>
  )
}
