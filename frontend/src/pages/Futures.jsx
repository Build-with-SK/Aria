import React from 'react'
import { useFutures } from '../hooks/useApi'
import { Spinner, ErrorBox, ScoreBadge, ActionBadge, MetricCard, SectionHeader } from '../components/UI'

export default function Futures() {
  const { data, loading, error } = useFutures()
  if (loading) return <Spinner />
  if (error)   return <ErrorBox message={error} />
  const all = Object.entries(data || {})
  const avgScore = all.length ? (all.reduce((s,[,f]) => s + (f.confirmation_score||0), 0) / all.length).toFixed(1) : 0
  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20 }}>Futures Analysis</h1>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginBottom: 20 }}>
        <MetricCard label="Contracts" value={all.length} />
        <MetricCard label="Avg Confirmation" value={`${avgScore > 0 ? '+' : ''}${avgScore}`} color={avgScore > 0 ? 'bull' : 'bear'} />
        <MetricCard label="Confirming Bull" value={all.filter(([,f]) => f.confirmation_score > 20).length} color="bull" />
      </div>
      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Contract</th><th>Name</th><th>Price</th><th>Conf. Score</th><th>Action</th><th>Regime</th><th>Vol</th></tr></thead>
          <tbody>
            {all.sort((a,b) => (b[1].confirmation_score||0)-(a[1].confirmation_score||0)).map(([ticker, f]) => (
              <tr key={ticker}>
                <td style={{ fontWeight: 600 }}>{ticker}</td>
                <td style={{ color: '#8b949e', fontSize: 12 }}>{f.name}</td>
                <td>${parseFloat(f.current_price||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}</td>
                <td><ScoreBadge score={f.confirmation_score} /></td>
                <td><ActionBadge action={f.action} /></td>
                <td style={{ color: '#8b949e', fontSize: 12 }}>{f.regime}</td>
                <td style={{ color: '#8b949e' }}>{((f.realised_vol||0)*100).toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
