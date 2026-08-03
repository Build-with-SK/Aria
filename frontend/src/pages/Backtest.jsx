import React, { useState } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'
import { useBacktest } from '../hooks/useApi'
import { Spinner, ErrorBox, SectionHeader, MetricCard } from '../components/UI'
import { useCurrency } from '../currency/CurrencyContext'

export default function Backtest() {
  const { data, loading, error } = useBacktest()
  const { price } = useCurrency()
  const [selected, setSelected] = useState(null)
  if (loading) return <Spinner />
  if (error)   return <ErrorBox message={error} />

  const results = data?.results || {}
  const sel = selected ? results[selected] : null
  const chartData = sel && sel.equity_dates ? sel.equity_dates.map((d, i) => ({ date: d, value: sel.equity_curve[i] })) : []

  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20 }}>Backtesting Results</h1>
      <div style={{ display: 'grid', gridTemplateColumns: sel ? '1fr 1.5fr' : '1fr', gap: 20 }}>
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>Asset</th><th>Return</th><th>Sharpe</th><th>Max DD</th><th>Win Rate</th><th>Trades</th></tr></thead>
            <tbody>
              {Object.entries(results).sort((a,b) => (b[1].metrics?.sharpe_ratio||0)-(a[1].metrics?.sharpe_ratio||0)).map(([ticker, bt]) => {
                const m = bt.metrics || {}
                return (
                  <tr key={ticker} onClick={() => setSelected(ticker === selected ? null : ticker)}
                    style={{ cursor: 'pointer', background: selected === ticker ? 'rgba(88,166,255,0.08)' : '' }}>
                    <td style={{ fontWeight: 600 }}>{ticker}</td>
                    <td style={{ color: (m.total_return||0) > 0 ? '#3fb950' : '#f85149' }}>{((m.total_return||0)*100).toFixed(1)}%</td>
                    <td style={{ color: (m.sharpe_ratio||0) > 1 ? '#3fb950' : (m.sharpe_ratio||0) > 0 ? '#d29922' : '#f85149' }}>{(m.sharpe_ratio||0).toFixed(2)}</td>
                    <td style={{ color: '#f85149' }}>{((m.max_drawdown||0)*100).toFixed(1)}%</td>
                    <td>{((m.win_rate||0)*100).toFixed(0)}%</td>
                    <td style={{ color: '#8b949e' }}>{m.n_trades||0}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {sel && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div className="card">
              <SectionHeader>{selected} — Performance</SectionHeader>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 16 }}>
                {[['Total Return', `${((sel.metrics?.total_return||0)*100).toFixed(1)}%`],
                  ['CAGR', `${((sel.metrics?.cagr||0)*100).toFixed(1)}%`],
                  ['Sharpe', (sel.metrics?.sharpe_ratio||0).toFixed(2)],
                  ['Sortino', (sel.metrics?.sortino_ratio||0).toFixed(2)],
                  ['Max DD', `${((sel.metrics?.max_drawdown||0)*100).toFixed(1)}%`],
                  ['Win Rate', `${((sel.metrics?.win_rate||0)*100).toFixed(0)}%`],
                ].map(([l,v]) => <MetricCard key={l} label={l} value={v} />)}
              </div>
              {chartData.length > 0 && (
                <ResponsiveContainer width="100%" height={200}>
                  <LineChart data={chartData}>
                    <CartesianGrid stroke="#21262d" />
                    <XAxis dataKey="date" tick={{ fill: '#8b949e', fontSize: 10 }} tickFormatter={d => d?.slice(5)} />
                    <YAxis tick={{ fill: '#8b949e', fontSize: 10 }} tickFormatter={v => price(v, { from: 'USD', digits: 0 }).text} />
                    <Tooltip contentStyle={{ background: '#161b22', border: '1px solid #30363d', color: '#e6edf3' }} formatter={v => [price(v, { from: 'USD', digits: 0 }).text, 'Portfolio']} />
                    <Line type="monotone" dataKey="value" stroke="#3fb950" dot={false} strokeWidth={2} />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
            {sel.trades?.length > 0 && (
              <div className="card" style={{ padding: 0 }}>
                <div style={{ padding: '12px 16px' }}><SectionHeader>Recent Trades</SectionHeader></div>
                <table>
                  <thead><tr><th>Entry</th><th>Exit</th><th>Dir</th><th>PnL%</th><th>Score</th></tr></thead>
                  <tbody>
                    {sel.trades.slice(-15).reverse().map((t, i) => (
                      <tr key={i}>
                        <td style={{ fontSize: 12 }}>{t.entry_date}</td>
                        <td style={{ fontSize: 12 }}>{t.exit_date}</td>
                        <td style={{ color: t.direction === 'long' ? '#3fb950' : '#f85149' }}>{t.direction}</td>
                        <td style={{ color: t.pnl_pct > 0 ? '#3fb950' : '#f85149', fontWeight: 600 }}>{(t.pnl_pct*100).toFixed(2)}%</td>
                        <td style={{ color: '#8b949e' }}>{t.signal_score?.toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
