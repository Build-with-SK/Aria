import React from 'react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { usePortfolio } from '../hooks/useApi'
import { Spinner, ErrorBox, SectionHeader, MetricCard } from '../components/UI'
import Term from '../components/Term'
import { useCurrency } from '../currency/CurrencyContext'

export default function Portfolio() {
  const { data, loading, error } = usePortfolio()
  const { price } = useCurrency()
  if (loading) return <Spinner />
  if (error)   return <ErrorBox message={error} />

  const analysis  = data?.analysis  || {}
  const optimised = data?.optimised || {}
  const exposure  = analysis.exposure || {}
  const byClass   = Object.entries(exposure.by_class || {})
  const riskContr = Object.entries(analysis.risk_contribution || {}).sort((a,b) => b[1]-a[1]).slice(0, 10)
  const warnings  = analysis.warnings || []

  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20 }}>Portfolio Risk</h1>
      {warnings.map((w, i) => (
        <div key={i} style={{ background: 'rgba(210,153,34,0.1)', border: '1px solid #d29922', borderRadius: 8, padding: '10px 14px', marginBottom: 12, fontSize: 13, color: '#d29922' }}>⚠️ {w}</div>
      ))}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        <MetricCard label="Active Positions" value={analysis.n_assets_active} />
        <MetricCard label="Total Allocated"  value={`${exposure.total_allocated?.toFixed(1)}%`} />
        <MetricCard label="Cash"             value={`${exposure.cash_pct?.toFixed(1)}%`} />
        <MetricCard label="Diversification"  value={`${analysis.diversification_score?.toFixed(0)}/100`} color={analysis.diversification_score > 60 ? 'bull' : 'bear'} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 20 }}>
        {byClass.length > 0 && (
          <div className="card">
            <SectionHeader>Exposure by Asset Class</SectionHeader>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={byClass.map(([k,v]) => ({ name: k, value: v }))}>
                <XAxis dataKey="name" tick={{ fill: '#8b949e', fontSize: 11 }} />
                <YAxis tick={{ fill: '#8b949e', fontSize: 11 }} />
                <Tooltip contentStyle={{ background: '#161b22', border: '1px solid #30363d', color: '#e6edf3' }} />
                <Bar dataKey="value" fill="#58a6ff" radius={[4,4,0,0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
        {riskContr.length > 0 && (
          <div className="card">
            <SectionHeader>Risk Contribution (Top 10)</SectionHeader>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={riskContr.map(([k,v]) => ({ name: k, value: v }))} layout="vertical">
                <XAxis type="number" tick={{ fill: '#8b949e', fontSize: 10 }} />
                <YAxis dataKey="name" type="category" tick={{ fill: '#8b949e', fontSize: 10 }} width={70} />
                <Tooltip contentStyle={{ background: '#161b22', border: '1px solid #30363d', color: '#e6edf3' }} />
                <Bar dataKey="value" fill="#f85149" radius={[0,4,4,0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
      {Object.entries(optimised).map(([strat, opt]) => (
        <div key={strat} className="card" style={{ marginBottom: 16 }}>
          <SectionHeader>Strategy: {strat.replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase())}</SectionHeader>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10, marginBottom: 14 }}>
            <MetricCard label="Exp. Return" value={`${((opt.expected_return||0)*100).toFixed(1)}%`} />
            <MetricCard label="Exp. Vol"    value={`${((opt.expected_vol||0)*100).toFixed(1)}%`} />
            <MetricCard label="Sharpe"      value={(opt.sharpe_ratio||0).toFixed(2)} />
            <MetricCard label="Allocated"   value={`${((opt.total_allocated||0)*100).toFixed(1)}%`} />
          </div>
          <table>
            <thead><tr><th><Term>Asset</Term></th><th><Term>Weight</Term></th><th>Amount</th><th><Term>Signal</Term></th><th><Term>Score</Term></th></tr></thead>
            <tbody>
              {(opt.allocation_table||[]).filter(r => r['Weight %'] > 0).slice(0, 10).map((r, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>{r.Ticker}</td>
                  <td style={{ color: '#58a6ff' }}>{r['Weight %']?.toFixed(1)}%</td>
                  <td style={{ color: '#8b949e' }}>{price(r['$ Allocation'], { from: 'USD', digits: 0 }).text}</td>
                  <td style={{ color: '#e6edf3' }}>{r.Signal}</td>
                  <td><span style={{ color: r.Score > 0 ? '#3fb950' : '#f85149', fontWeight: 600 }}>{r.Score > 0 ? '+' : ''}{r.Score?.toFixed(1)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}
