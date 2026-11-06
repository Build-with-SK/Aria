import React, { useEffect, useState } from 'react'
import axios from 'axios'

const mono = { fontFamily: 'var(--mono)' }
const panel = { ...mono, border: '1px solid var(--border)', padding: 14, marginBottom: 14 }

export function MultiassetView({ data, loading = false, error = false }) {
  if (loading) return <div role="status" style={panel}>Loading multiasset research state…</div>
  if (error || !data || data.schema_version !== '1') return (
    <div role="status" style={panel}>
      <strong style={{ color: 'var(--orange)' }}>Multiasset state unavailable</strong>
      <p>Account, rules and connection status could not be verified. Refresh to retry.</p>
    </div>
  )
  const available = value => value == null ? 'Unavailable' : String(value)
  return (
    <section aria-label="Multiasset research" style={panel}>
      <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 8 }}>
        <h2 style={{ fontSize: 13, margin: 0, color: 'var(--orange)' }}>MULTIASSET RESEARCH</h2>
        <span style={{ color: 'var(--orange)', fontSize: 11 }}>EXECUTION {data.execution_mode || 'UNAVAILABLE'}</span>
      </div>
      <p style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.6 }}>
        Research proposals require independent risk checks and human review. This workspace has no order sender.
        Existing desk and broker controls appear separately below.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(155px,1fr))', gap: 12, fontSize: 11 }}>
        {[
          ['Environment', data.account?.environment], ['Balance', data.balance], ['Equity', data.equity],
          ['Daily drawdown', data.daily_drawdown], ['Total drawdown', data.total_drawdown],
          ['Risk budget', data.remaining_risk_budget], ['Account rules', data.rule_profile?.status],
          ['Market data', data.data_state], ['MT5 connection', data.mt5?.status],
          ['Reconciliation', data.reconciliation],
        ].map(([label, value]) => <div key={label}>
          <div style={{ color: 'var(--muted)', marginBottom: 5 }}>{label}</div>
          <div>{available(value)}</div>
        </div>)}
      </div>
      <div role="status" style={{ margin: '16px 0', color: 'var(--green)', fontSize: 12 }}>
        {data.decision?.status === 'ABSTAIN' ? 'NO TRADE — ' : 'REVIEW STATE — '}{data.decision?.reason || 'Decision unavailable'}
      </div>
      <details>
        <summary style={{ cursor: 'pointer', fontSize: 11 }}>Research coverage and strategy lab</summary>
        <div style={{ overflowX: 'auto', marginTop: 12 }}>
          <table style={{ width: '100%', textAlign: 'left', fontSize: 11, borderCollapse: 'collapse' }}>
            <thead><tr>{['Asset class', 'Research features', 'Broker tradability'].map(h =>
              <th key={h} scope="col" style={{ padding: 8, color: 'var(--muted)' }}>{h}</th>)}</tr></thead>
            <tbody>{(data.research_universe || []).map(row => <tr key={row.asset_class}>
              <td style={{ padding: 8 }}>{row.asset_class}</td>
              <td style={{ padding: 8 }}>{row.features?.join(', ') || 'Unsupported'}</td>
              <td style={{ padding: 8 }}>{row.broker_tradability || 'UNVERIFIED'}</td>
            </tr>)}</tbody>
          </table>
        </div>
        <p style={{ fontSize: 11, color: 'var(--muted)', lineHeight: 1.6 }}>
          {(data.strategies || []).map(s => s.family).join(' · ')}
        </p>
        <p style={{ fontSize: 11 }}>These are research definitions. No validated edge or broker trading permission is implied.</p>
      </details>
      <p style={{ color: 'var(--muted)', fontSize: 10, marginBottom: 0 }}>
        Updated {data.as_of || 'Unavailable'} · Third-party plugins: {data.third_party_plugins || 'UNAVAILABLE'}
      </p>
    </section>
  )
}

export default function MultiassetWorkspace() {
  const [state, setState] = useState({ data: null, loading: true, error: false })
  useEffect(() => {
    let disposed = false
    const controller = new AbortController()
    const poll = async () => {
      try {
        const response = await axios.get('/api/trading/workspace', { signal: controller.signal, timeout: 10000 })
        if (!disposed) setState({ data: response.data, loading: false, error: false })
      } catch {
        if (!disposed) setState({ data: null, loading: false, error: true })
      }
    }
    poll()
    const interval = setInterval(poll, 30000)
    return () => { disposed = true; controller.abort(); clearInterval(interval) }
  }, [])
  return <MultiassetView {...state} />
}
