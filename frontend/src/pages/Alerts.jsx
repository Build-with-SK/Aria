import React, { useState } from 'react'
import { useAlerts } from '../hooks/useApi'
import { Spinner, ErrorBox, SectionHeader, SeverityBadge, MetricCard } from '../components/UI'

export default function Alerts() {
  const { data, loading, error } = useAlerts()
  const [filter, setFilter] = useState('all')
  if (loading) return <Spinner />
  if (error)   return <ErrorBox message={error} />
  const all = Array.isArray(data?.alerts) ? data.alerts : []
  const filtered = filter === 'all' ? all : all.filter(a => a.severity === filter)
  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20 }}>Alerts</h1>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12, marginBottom: 20 }}>
        <MetricCard label="Total"    value={data?.count}    />
        <MetricCard label="Critical" value={data?.critical} color="bear" />
        <MetricCard label="Warning"  value={data?.warning}  color="neut" />
        <MetricCard label="Info"     value={data?.info}     color="acc"  />
      </div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {['all','CRITICAL','WARNING','INFO'].map(f => (
          <button key={f} onClick={() => setFilter(f)}
            style={{ padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: 'none',
              background: filter === f ? '#58a6ff' : '#21262d', color: filter === f ? '#fff' : '#8b949e' }}>
            {f}
          </button>
        ))}
      </div>
      <div className="card" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Severity</th><th>Asset</th><th>Alert</th><th>Message</th></tr></thead>
          <tbody>
            {filtered.length === 0
              ? <tr><td colSpan={4} style={{ textAlign: 'center', color: '#8b949e', padding: 24 }}>✅ No alerts</td></tr>
              : filtered.map((a, i) => (
                <tr key={i}>
                  <td><SeverityBadge severity={a.severity} /></td>
                  <td style={{ color: '#58a6ff', fontWeight: 600 }}>{a.ticker || 'Market'}</td>
                  <td style={{ fontWeight: 500 }}>{a.title}</td>
                  <td style={{ color: '#8b949e', fontSize: 12 }}>{(a.message||'').slice(0, 100)}</td>
                </tr>
              ))
            }
          </tbody>
        </table>
      </div>
    </div>
  )
}
