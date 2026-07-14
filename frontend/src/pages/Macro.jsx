import React from 'react'
import { useMacro } from '../hooks/useApi'
import { Spinner, ErrorBox, SectionHeader, MetricCard } from '../components/UI'

export default function Macro() {
  const { data, loading, error } = useMacro()
  if (loading) return <Spinner />
  if (error)   return <ErrorBox message={error} />
  const d = data || {}
  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20 }}>Macro Environment</h1>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12, marginBottom: 20 }}>
        <MetricCard label="Regime"      value={d.regime} />
        <MetricCard label="Macro Score" value={d.macro_score != null ? `${d.macro_score > 0 ? '+' : ''}${d.macro_score?.toFixed(1)}` : '—'} color={d.macro_score > 0 ? 'bull' : 'bear'} />
        <MetricCard label="VIX"         value={d.vix}    color={d.vix > 25 ? 'bear' : d.vix < 15 ? 'bull' : 'neut'} />
        <MetricCard label="DXY"         value={d.dxy} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        <div className="card">
          <SectionHeader>Interest Rates</SectionHeader>
          {[['Fed Funds Rate', d.fed_funds_rate != null ? `${d.fed_funds_rate?.toFixed(2)}%` : '—'],
            ['10Y Treasury',   d.treasury_10y   != null ? `${d.treasury_10y?.toFixed(3)}%`  : '—'],
            ['2Y Treasury',    d.treasury_2y    != null ? `${(d.treasury_2y*100)?.toFixed(2)}%` : '—'],
            ['Yield Spread 10Y-2Y', d.yield_spread_10y2y != null ? `${d.yield_spread_10y2y?.toFixed(3)}%` : '—'],
          ].map(([l,v]) => (
            <div key={l} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid #21262d' }}>
              <span style={{ color: '#8b949e', fontSize: 13 }}>{l}</span>
              <span style={{ fontWeight: 600 }}>{v}</span>
            </div>
          ))}
        </div>
        <div className="card">
          <SectionHeader>Economic Indicators</SectionHeader>
          {[['CPI YoY',          d.cpi_yoy          != null ? `${d.cpi_yoy?.toFixed(1)}%`          : '—'],
            ['Unemployment',     d.unemployment_rate != null ? `${d.unemployment_rate?.toFixed(1)}%` : '—'],
            ['DXY 20D Trend',    d.dxy_trend         != null ? `${(d.dxy_trend*100)?.toFixed(2)}%`  : '—'],
            ['Gold 20D Trend',   d.gold_trend_20d    != null ? `${(d.gold_trend_20d*100)?.toFixed(2)}%` : '—'],
          ].map(([l,v]) => (
            <div key={l} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid #21262d' }}>
              <span style={{ color: '#8b949e', fontSize: 13 }}>{l}</span>
              <span style={{ fontWeight: 600 }}>{v}</span>
            </div>
          ))}
        </div>
      </div>
      {d.warnings?.length > 0 && (
        <div className="card" style={{ marginTop: 20 }}>
          <SectionHeader>Macro Warnings</SectionHeader>
          {d.warnings.map((w, i) => <div key={i} style={{ color: '#d29922', fontSize: 13, marginBottom: 6 }}>⚠️ {w}</div>)}
        </div>
      )}
      <div style={{ marginTop: 12, padding: '10px 14px', background: '#161b22', border: '1px solid #30363d', borderRadius: 8, fontSize: 11, color: '#8b949e' }}>
        Data sources: {d.sources_available?.join(', ') || 'yfinance'} · Last updated: {d.data_date}
      </div>
    </div>
  )
}
