import React from 'react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { usePortfolio, useHoldings, useRelated } from '../hooks/useApi'
import { Spinner, ErrorBox, SectionHeader, MetricCard } from '../components/UI'
import Term from '../components/Term'

// ─── Portfolio ────────────────────────────────────────────────────────────────
// WHAT HE OWNS, FIRST. This page used to open on data/portfolio_analysis.json:
// a fixed model universe of AAPL, ^GSPC, EURUSD=X, GC=F and twenty others,
// last computed on 31 May, none of it ever bought. The owner's report was
// exact — he did not recognise any of the stocks, because none of them were
// his.
//
// So the account leads, the model universe follows, and the model universe is
// labelled as what it is rather than left to look like a statement.

const mono = { fontFamily: 'var(--mono)' }
const dim = { color: 'var(--text-dim)' }

const money = (n, ccy = 'USD') =>
  n === null || n === undefined
    ? '—'
    : `${ccy === 'GBP' ? '£' : '$'}${Number(n).toLocaleString(undefined, {
        minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const pnlColour = n =>
  n === null || n === undefined ? 'var(--text-dim)'
    : n > 0 ? 'var(--green)' : n < 0 ? '#f85149' : 'var(--text-dim)'

function Holdings({ data }) {
  const holdings = data?.holdings || []
  const totals = data?.totals || {}
  const account = data?.account || {}

  if (!holdings.length) {
    return (
      <div className="card" style={{ marginBottom: 20 }}>
        <SectionHeader>Holdings</SectionHeader>
        <div style={{ ...mono, fontSize: 12, ...dim, padding: '8px 0' }}>
          {data?.note || 'No open positions.'}
        </div>
      </div>
    )
  }

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <SectionHeader>
        Holdings — {totals.positions} position{totals.positions === 1 ? '' : 's'}
        {account.paper && (
          <span style={{ ...mono, fontSize: 10, marginLeft: 8, color: 'var(--orange)' }}>
            PAPER
          </span>
        )}
      </SectionHeader>
      <div style={{ overflowX: 'auto' }}>
        <table>
          <thead>
            <tr>
              <th><Term>Asset</Term></th>
              <th>Qty</th>
              <th>Avg cost</th>
              <th>Last</th>
              <th>Value</th>
              <th><Term>P&amp;L</Term></th>
              <th><Term>Weight</Term></th>
              <th>Held</th>
              <th>Stop / target</th>
            </tr>
          </thead>
          <tbody>
            {holdings.map(h => (
              <tr key={h.ticker}>
                <td>
                  <div style={{ fontWeight: 600 }}>{h.ticker}</div>
                  <div style={{ ...mono, fontSize: 10, ...dim }}>
                    {h.name || '—'}
                    {h.asset_class ? ` · ${h.asset_class}` : ''}
                  </div>
                </td>
                <td style={mono}>{h.qty}</td>
                <td style={mono}>{money(h.avg_cost, h.currency)}</td>
                <td style={mono}>{money(h.last_price, h.currency)}</td>
                <td style={mono}>{money(h.market_value, h.currency)}</td>
                <td style={{ ...mono, color: pnlColour(h.unrealised_pl), fontWeight: 600 }}>
                  {h.unrealised_pl > 0 ? '+' : ''}{money(h.unrealised_pl, h.currency)}
                  {h.unrealised_pl_pct !== null && h.unrealised_pl_pct !== undefined && (
                    <span style={{ fontSize: 10, marginLeft: 4 }}>
                      ({h.unrealised_pl_pct > 0 ? '+' : ''}{h.unrealised_pl_pct}%)
                    </span>
                  )}
                </td>
                <td style={mono}>{h.weight_pct === null ? '—' : `${h.weight_pct}%`}</td>
                <td style={{ ...mono, ...dim }}>
                  {h.days_held === null || h.days_held === undefined ? '—' : `${h.days_held}d`}
                </td>
                <td style={{ ...mono, fontSize: 11 }}>
                  {h.stop ? money(h.stop, h.currency) : '—'}
                  {' / '}
                  {h.target ? money(h.target, h.currency) : '—'}
                  {h.distance_to_stop_pct !== undefined && (
                    <div style={{ fontSize: 10, ...dim }}>
                      {h.distance_to_stop_pct}% above stop
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {holdings.some(h => h.thesis) && (
        <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          {holdings.filter(h => h.thesis).map(h => (
            <div key={h.ticker} style={{ marginBottom: 10 }}>
              <span style={{ ...mono, fontSize: 10, color: 'var(--orange)', letterSpacing: 1 }}>
                WHY {h.ticker}
              </span>
              <div style={{ ...mono, fontSize: 11, ...dim, marginTop: 3, lineHeight: 1.55 }}>
                {h.thesis}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* An honest line about where the numbers came from, on every render. */}
      <div style={{ ...mono, fontSize: 10, ...dim, marginTop: 10 }}>
        {data.note} {holdings[0]?.price_source ? `· prices: ${holdings[0].price_source}` : ''}
      </div>
    </div>
  )
}

function Related({ data }) {
  const groups = (data?.groups || []).filter(g => (g.related || []).length)
  if (!groups.length) return null

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <SectionHeader>Related to what he holds</SectionHeader>
      {groups.map(g => (
        <div key={g.holding} style={{ marginBottom: 16 }}>
          <div style={{ ...mono, fontSize: 11, marginBottom: 6 }}>
            <span style={{ color: 'var(--orange)', fontWeight: 700 }}>{g.holding}</span>
            <span style={dim}> — {g.holding_name} · {g.weight_pct}% of book</span>
          </div>
          <table>
            <thead>
              <tr><th>Instrument</th><th>Relationship</th><th>Note</th></tr>
            </thead>
            <tbody>
              {g.related.map(r => (
                <tr key={r.ticker}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{r.ticker}</div>
                    <div style={{ ...mono, fontSize: 10, ...dim }}>{r.name}</div>
                  </td>
                  <td style={{ ...mono, fontSize: 11, ...dim }}>{r.relationship}</td>
                  <td style={{ ...mono, fontSize: 11 }}>
                    {r.already_held && (
                      <span style={{ color: 'var(--green)' }}>already held</span>
                    )}
                    {r.concentration_warning && (
                      <div style={{ color: 'var(--orange)' }}>{r.concentration_warning}</div>
                    )}
                    {r.scored && r.direction && (
                      <span style={{ color: 'var(--text-dim)' }}>{r.direction}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      <div style={{ ...mono, fontSize: 10, ...dim, marginTop: 4 }}>{data.note}</div>
    </div>
  )
}

export default function Portfolio() {
  const { data: book, loading, error } = useHoldings()
  const { data: related } = useRelated()
  const { data: model } = usePortfolio()

  if (loading) return <Spinner />
  if (error) return <ErrorBox message={error} />

  const account = book?.account || {}
  const totals = book?.totals || {}
  const analysis = model?.analysis || {}
  const byClass = Object.entries(totals.by_asset_class || {})

  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20 }}>Portfolio</h1>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        <MetricCard label="Equity" value={money(account.equity)} />
        <MetricCard label="Invested" value={money(totals.invested)} />
        <MetricCard
          label="Unrealised P&L"
          value={`${totals.unrealised_pl > 0 ? '+' : ''}${money(totals.unrealised_pl)}`}
          color={totals.unrealised_pl > 0 ? 'bull' : totals.unrealised_pl < 0 ? 'bear' : undefined}
        />
        <MetricCard label="Cash" value={totals.cash_pct === null ? '—' : `${totals.cash_pct}%`} />
      </div>

      <Holdings data={book} />
      <Related data={related} />

      {byClass.length > 0 && (
        <div className="card" style={{ marginBottom: 20 }}>
          <SectionHeader>Exposure by asset class</SectionHeader>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={byClass.map(([k, v]) => ({ name: k, value: v }))}>
              <XAxis dataKey="name" tick={{ fill: '#8b949e', fontSize: 11 }} />
              <YAxis tick={{ fill: '#8b949e', fontSize: 11 }} />
              <Tooltip contentStyle={{ background: '#161b22', border: '1px solid #30363d', color: '#e6edf3' }} />
              <Bar dataKey="value" fill="#58a6ff" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* The old page. Kept, because the risk maths is useful — but labelled,
          because presenting a model universe next to real holdings without
          saying which is which is how the confusion started. */}
      {analysis.diversification_score !== undefined && (
        <div className="card">
          <SectionHeader>Model universe — not his holdings</SectionHeader>
          <div style={{ ...mono, fontSize: 11, ...dim, marginBottom: 12, lineHeight: 1.6 }}>
            Risk maths over a fixed analysis universe of {analysis.n_assets_active || '—'} assets,
            last computed {String(analysis.generated_at || '').slice(0, 10) || 'some time ago'}.
            None of it is owned. It is here for the correlation and
            diversification work, not as a statement of position.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
            <MetricCard label="Diversification" value={`${analysis.diversification_score?.toFixed(0)}/100`} />
            <MetricCard label="Portfolio vol" value={`${((analysis.portfolio_vol || 0) * 100).toFixed(1)}%`} />
            <MetricCard label="Assets" value={analysis.n_assets_active} />
          </div>
        </div>
      )}
    </div>
  )
}
