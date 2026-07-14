/**
 * pages/Overview.jsx
 * Main dashboard overview page.
 */

import React, { useState } from 'react'
import { RadarChart, PolarGrid, PolarAngleAxis, Radar, ResponsiveContainer,
         LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts'
import { useSummary, useSignals, useAlerts, useMacro, triggerRun } from '../hooks/useApi'
import { MetricCard, ScoreBadge, ActionBadge, SectionHeader, SeverityBadge, Spinner, ErrorBox } from '../components/UI'

export default function Overview() {
  const { data: summary, loading: sl, error: se } = useSummary()
  const { data: signals, loading: sigl }          = useSignals()
  const { data: alerts }                          = useAlerts()
  const { data: macro }                           = useMacro()
  const [running, setRunning] = useState(false)

  if (sl) return <Spinner />
  if (se) return <ErrorBox message={se} />

  const allSignals  = Object.values(signals?.signals || {})
  const topBull     = allSignals.sort((a, b) => b.composite_score - a.composite_score).slice(0, 8)
  const topBear     = [...allSignals].sort((a, b) => a.composite_score - b.composite_score).slice(0, 5)
  const alertsList  = Array.isArray(alerts?.alerts) ? alerts.alerts : []
  const critAlerts  = alertsList.filter(a => a.severity === 'CRITICAL')

  const handleRun = async () => {
    setRunning(true)
    await triggerRun(true)
    setTimeout(() => setRunning(false), 3000)
  }

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: '#e6edf3' }}>Market Overview</h1>
          <div style={{ fontSize: 12, color: '#8b949e', marginTop: 2 }}>
            {summary?.market_regime} · VIX: {summary?.vix ?? '—'} · DXY: {summary?.dxy ?? '—'}
          </div>
        </div>
        <button
          onClick={handleRun}
          disabled={running}
          style={{
            background: running ? '#21262d' : '#238636', color: '#fff',
            border: 'none', borderRadius: 6, padding: '8px 16px',
            fontSize: 13, fontWeight: 600, cursor: running ? 'not-allowed' : 'pointer',
          }}
        >
          {running ? '⏳ Running...' : '▶ Run Pipeline'}
        </button>
      </div>

      {/* Critical alerts banner */}
      {critAlerts.length > 0 && (
        <div style={{ background: 'rgba(248,81,73,0.1)', border: '1px solid #f85149', borderRadius: 8, padding: '12px 16px', marginBottom: 20 }}>
          <div style={{ color: '#f85149', fontWeight: 600, fontSize: 13 }}>🔴 {critAlerts.length} Critical Alert{critAlerts.length > 1 ? 's' : ''}</div>
          {critAlerts.slice(0, 2).map((a, i) => (
            <div key={i} style={{ color: '#e6edf3', fontSize: 12, marginTop: 4 }}>{a.title}</div>
          ))}
        </div>
      )}

      {/* Key metrics */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12, marginBottom: 24 }}>
        <MetricCard label="Bullish"    value={summary?.bullish}          color="bull" />
        <MetricCard label="Bearish"    value={summary?.bearish}          color="bear" />
        <MetricCard label="Neutral"    value={summary?.neutral}          color="neut" />
        <MetricCard label="VIX"        value={summary?.vix ?? '—'}       color={summary?.vix > 25 ? 'bear' : 'bull'} />
        <MetricCard label="Macro Score" value={summary?.macro_score != null ? `${summary.macro_score > 0 ? '+' : ''}${summary.macro_score.toFixed(1)}` : '—'} />
        <MetricCard label="Alerts"     value={`${summary?.alerts_critical ?? 0}C / ${summary?.alerts_warning ?? 0}W`} color={summary?.alerts_critical > 0 ? 'bear' : 'muted'} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 20, marginBottom: 24 }}>
        {/* Top Bullish */}
        <div className="card">
          <SectionHeader>🟢 Top Bullish Signals</SectionHeader>
          <table>
            <thead>
              <tr>
                <th>Asset</th><th>Score</th><th>Signal</th><th>Conf</th><th>Vol</th>
              </tr>
            </thead>
            <tbody>
              {topBull.map(s => (
                <tr key={s.ticker}>
                  <td style={{ fontWeight: 600 }}>{s.ticker}</td>
                  <td><ScoreBadge score={s.composite_score} /></td>
                  <td><ActionBadge action={s.action} /></td>
                  <td style={{ color: '#8b949e' }}>{s.confidence}</td>
                  <td style={{ color: '#8b949e' }}>{(s.realised_vol * 100).toFixed(0)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Macro panel */}
        <div className="card">
          <SectionHeader>🌐 Macro Environment</SectionHeader>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {[
              ['Regime',       macro?.regime],
              ['Macro Score',  macro?.macro_score != null ? `${macro.macro_score > 0 ? '+' : ''}${macro.macro_score.toFixed(1)}` : '—'],
              ['VIX',          macro?.vix],
              ['DXY',          macro?.dxy],
              ['10Y Yield',    macro?.treasury_10y ? `${macro.treasury_10y.toFixed(2)}%` : '—'],
              ['Yield Spread', macro?.yield_spread_10y2y != null ? `${macro.yield_spread_10y2y.toFixed(2)}%` : '—'],
              ['CPI YoY',      macro?.cpi_yoy != null ? `${macro.cpi_yoy.toFixed(1)}%` : '—'],
              ['Unemployment', macro?.unemployment_rate != null ? `${macro.unemployment_rate.toFixed(1)}%` : '—'],
            ].map(([label, val]) => (
              <div key={label} style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#8b949e', fontSize: 12 }}>{label}</span>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{val ?? '—'}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Alerts */}
      {alertsList.length > 0 && (
        <div className="card">
          <SectionHeader>⚠️ Active Alerts</SectionHeader>
          <table>
            <thead>
              <tr><th>Severity</th><th>Asset</th><th>Alert</th><th>Message</th></tr>
            </thead>
            <tbody>
              {alertsList.slice(0, 8).map((a, i) => (
                <tr key={i}>
                  <td><SeverityBadge severity={a.severity} /></td>
                  <td style={{ color: '#58a6ff', fontWeight: 600 }}>{a.ticker || 'Market'}</td>
                  <td style={{ fontWeight: 500 }}>{a.title}</td>
                  <td style={{ color: '#8b949e', fontSize: 12 }}>{(a.message || '').slice(0, 80)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
