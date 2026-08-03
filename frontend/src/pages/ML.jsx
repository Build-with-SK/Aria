import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { useML } from '../hooks/useApi'
import { Spinner, ErrorBox, SectionHeader, MetricCard } from '../components/UI'

export default function ML() {
  const { data, loading, error } = useML()
  const [selected, setSelected] = useState(null)
  if (loading) return <Spinner />
  if (error)   return <ErrorBox message={error} />

  const all = Object.entries(data || {})
  const trained = all.filter(([,p]) => p.models_trained)
  const sel = selected ? data[selected] : null

  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20 }}>ML Predictions</h1>
      <div style={{ display: 'grid', gridTemplateColumns: sel ? '1.4fr 1fr' : '1fr', gap: 20 }}>
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>Asset</th><th>Overall</th><th>1D Bull%</th><th>5D Bull%</th><th>20D Bull%</th><th>Trained</th></tr></thead>
            <tbody>
              {all.sort((a,b) => (b[1].overall_bullish||0.5)-(a[1].overall_bullish||0.5)).map(([ticker, pred]) => {
                const h = pred.horizons || {}
                const d1 = h['1']?.bullish_prob, d5 = h['5']?.bullish_prob, d20 = h['20']?.bullish_prob
                const sig = pred.overall_signal
                return (
                  <tr key={ticker} onClick={() => setSelected(ticker === selected ? null : ticker)}
                    style={{ cursor: 'pointer', background: selected === ticker ? 'rgba(88,166,255,0.08)' : '' }}>
                    <td>
                      <Link to={`/research?symbol=${encodeURIComponent(ticker)}`}
                        onClick={e => e.stopPropagation()}
                        title={`Open ${ticker} research`}
                        style={{ fontWeight: 600, color: 'var(--orange)', textDecoration: 'none' }}>
                        {ticker}
                      </Link>
                    </td>
                    <td style={{ color: sig === 'Bullish' ? '#3fb950' : sig === 'Bearish' ? '#f85149' : '#d29922', fontWeight: 600 }}>{sig}</td>
                    <td style={{ color: d1 > 0.55 ? '#3fb950' : d1 < 0.45 ? '#f85149' : '#8b949e' }}>{d1 ? `${(d1*100).toFixed(0)}%` : '—'}</td>
                    <td style={{ color: d5 > 0.55 ? '#3fb950' : d5 < 0.45 ? '#f85149' : '#8b949e' }}>{d5 ? `${(d5*100).toFixed(0)}%` : '—'}</td>
                    <td style={{ color: d20 > 0.55 ? '#3fb950' : d20 < 0.45 ? '#f85149' : '#8b949e' }}>{d20 ? `${(d20*100).toFixed(0)}%` : '—'}</td>
                    <td>{pred.models_trained ? '✅' : '❌'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {sel && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div className="card">
              <SectionHeader>{selected} — ML Detail</SectionHeader>
              <MetricCard label="Overall Signal" value={sel.overall_signal} color={sel.overall_signal === 'Bullish' ? 'bull' : sel.overall_signal === 'Bearish' ? 'bear' : 'neut'} />
              <div style={{ marginTop: 14 }}>
                {Object.entries(sel.horizons || {}).map(([h, hp]) => (
                  <div key={h} className="card" style={{ marginBottom: 10 }}>
                    <div style={{ fontWeight: 600, marginBottom: 8 }}>{h}-Day Horizon</div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                      <MetricCard label="Bull Prob" value={`${(hp.bullish_prob*100).toFixed(0)}%`} color="bull" />
                      <MetricCard label="Bear Prob" value={`${(hp.bearish_prob*100).toFixed(0)}%`} color="bear" />
                      <MetricCard label="Confidence" value={hp.confidence} />
                      <MetricCard label="Model Agree" value={`${(hp.model_agreement*100).toFixed(0)}%`} />
                    </div>
                    {hp.per_model_probs && (
                      <div style={{ marginTop: 10 }}>
                        {Object.entries(hp.per_model_probs).map(([m, p]) => (
                          <div key={m} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                            <span style={{ color: '#8b949e' }}>{m}</span>
                            <span style={{ color: p > 0.55 ? '#3fb950' : p < 0.45 ? '#f85149' : '#d29922' }}>{(p*100).toFixed(0)}%</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
