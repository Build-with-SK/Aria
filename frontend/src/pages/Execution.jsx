import React, { useState } from 'react'
import { useCurrency } from '../currency/CurrencyContext'
import { BBCard, SectionHeader, ScoreBadge, ActionBadge } from '../components/UI'
import {
  useApprovalQueue, useBrokerStatus, useLivePositions,
  approveAndExecute, rejectTrade, cancelTrade
} from '../hooks/useExecution'

// ─── Status pill ─────────────────────────────────────────────────────────────
function StatusPill({ status }) {
  const cfg = {
    pending:   { color: 'var(--yellow)', bg: 'color-mix(in srgb, var(--yellow) 12%, transparent)',   label: '⏳ Pending approval' },
    approved:  { color: 'var(--blue)', bg: 'color-mix(in srgb, var(--blue) 12%, transparent)',  label: '✓ Approved' },
    executed:  { color: 'var(--green)', bg: 'color-mix(in srgb, var(--green) 12%, transparent)',   label: '✅ Executed' },
    rejected:  { color: 'var(--red)', bg: 'color-mix(in srgb, var(--red) 12%, transparent)',  label: '✗ Rejected' },
    cancelled: { color: 'var(--ink-3)',    bg: 'var(--inset)', label: '— Cancelled' },
  }
  const c = cfg[status] || cfg.cancelled
  return (
    <span style={{
      fontFamily: 'var(--font)', fontSize: 13, fontWeight: 600,
      color: c.color, background: c.bg,
      border: `1px solid ${c.color}44`,
      borderRadius: 8, padding: '2px 8px', 
    }}>{c.label}</span>
  )
}

// ─── Broker status panel ──────────────────────────────────────────────────────
function BrokerPanel() {
  const { price } = useCurrency()
  const { data, loading } = useBrokerStatus()

  const BrokerCard = ({ name, info }) => (
    <div style={{
      background: 'var(--inset)', border: `1px solid ${info?.connected ? 'var(--green)' : 'var(--line)'}`,
      borderRadius: 12, padding: '14px 18px', flex: 1,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontFamily: 'var(--font)', fontSize: 14, color: 'var(--accent)', fontWeight: 600 }}>
          {name}
        </span>
        <span style={{
          fontSize: 13, fontFamily: 'var(--font)', fontWeight: 600,
          color: info?.connected ? 'var(--green)' : 'var(--red)',
        }}>
          {info?.connected ? '● Live' : '○ Offline'}
          {info?.paper ? ' (Paper)' : ''}
        </span>
      </div>
      {info?.account ? (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {[
            ['Cash', price(info.account.cash, { from: 'USD', digits: 0 }).text],
            ['Portfolio', price(info.account.portfolio_value, { from: 'USD', digits: 0 }).text],
            ['Buying Power', price(info.account.buying_power, { from: 'USD', digits: 0 }).text],
          ].map(([label, val]) => (
            <div key={label}>
              <div style={{ color: 'var(--ink-2)', fontSize: 12, fontFamily: 'var(--font)' }}>{label}</div>
              <div style={{ color: 'var(--ink)', fontSize: 15, fontFamily: 'var(--font)', fontWeight: 600 }}>{val}</div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: 'var(--ink-2)', fontSize: 14, fontFamily: 'var(--font)' }}>
          {info?.connected ? 'Loading account…' : 'Broker not connected — no brokerage credentials are configured on the server.'}
        </div>
      )}
    </div>
  )

  return (
    <div style={{ display: 'flex', gap: 16, marginBottom: 24 }}>
      <BrokerCard name="Alpaca" info={loading ? null : data?.alpaca} />
      <BrokerCard name="IBKR"   info={loading ? null : data?.ibkr}   />
    </div>
  )
}

// ─── Live positions table ─────────────────────────────────────────────────────
function PositionsTable() {
  const data = useLivePositions()
  if (!data?.positions?.length) return null

  return (
    <BBCard title="Live positions" style={{ marginBottom: 24 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font)', fontSize: 14 }}>
        <thead>
          <tr style={{ color: 'var(--ink-2)', textAlign: 'left' }}>
            {['Ticker', 'Side', 'Qty', 'Avg Cost', 'Market Value', 'Unrealized P&L', 'Broker'].map(h => (
              <th key={h} style={{ padding: '6px 10px', borderBottom: '1px solid var(--line)', fontWeight: 600, fontSize: 12 }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.positions.map((p, i) => (
            <tr key={i} style={{ borderBottom: '1px solid var(--inset)' }}>
              <td style={{ padding: '8px 10px', color: 'var(--accent)', fontWeight: 600 }}>{p.ticker}</td>
              <td style={{ padding: '8px 10px', color: p.side === 'long' ? 'var(--green)' : 'var(--red)', }}>{p.side}</td>
              <td style={{ padding: '8px 10px', color: 'var(--ink)' }}>{p.qty}</td>
              <td style={{ padding: '8px 10px', color: 'var(--ink)' }}>${Number(p.avg_cost).toFixed(2)}</td>
              <td style={{ padding: '8px 10px', color: 'var(--ink)' }}>${Number(p.market_value).toLocaleString()}</td>
              <td style={{ padding: '8px 10px', color: p.unrealized_pl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                {p.unrealized_pl >= 0 ? '+' : ''}${Number(p.unrealized_pl).toFixed(2)}
              </td>
              <td style={{ padding: '8px 10px', color: 'var(--ink-2)' }}>{p.broker}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </BBCard>
  )
}

// ─── Single trade card ────────────────────────────────────────────────────────
function TradeCard({ trade, onAction }) {
  const { price } = useCurrency()
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState('')
  const [rejectReason, setRejectReason] = useState('')
  const [showReject, setShowReject]     = useState(false)

  const isPending = trade.status === 'pending'

  const handle = async (action) => {
    setLoading(true)
    setError('')
    try {
      if (action === 'approve') await approveAndExecute(trade.id)
      else if (action === 'reject') await rejectTrade(trade.id, rejectReason)
      else if (action === 'cancel') await cancelTrade(trade.id)
      onAction()
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }

  const sideColor = trade.side === 'buy' ? 'var(--green)' : 'var(--red)'
  const riskRatio = trade.take_profit && trade.stop_loss && trade.current_price
    ? Math.abs((trade.take_profit - trade.est_value / trade.qty) / (trade.est_value / trade.qty - trade.stop_loss)).toFixed(1)
    : null

  return (
    <div style={{
      background: 'var(--inset)',
      border: `1px solid ${isPending ? 'var(--accent)' : 'var(--line)'}`,
      borderLeft: `4px solid ${isPending ? 'var(--accent)' : trade.status === 'executed' ? 'var(--green)' : trade.status === 'rejected' ? 'var(--red)' : 'var(--line)'}`,
      borderRadius: 12, padding: 20, marginBottom: 16,
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <span style={{ fontFamily: 'var(--font)', fontSize: 26, fontWeight: 600, color: 'var(--accent)' }}>
            {trade.ticker}
          </span>
          <span style={{
            fontFamily: 'var(--font)', fontSize: 17, fontWeight: 600, color: sideColor,
          }}>
            {trade.side === 'buy' ? '▲ Buy' : '▼ Sell'}
          </span>
          <span style={{ fontFamily: 'var(--font)', fontSize: 15, color: 'var(--ink)' }}>
            {trade.qty} {trade.asset_class === 'crypto' ? 'units' : 'shares'}
          </span>
          {trade.order_type !== 'market' && (
            <span style={{ fontFamily: 'var(--font)', fontSize: 13, color: 'var(--ink-2)', }}>
              [{trade.order_type}]
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <StatusPill status={trade.status} />
          <span style={{ fontFamily: 'var(--font)', fontSize: 12, color: 'var(--ink-2)' }}>
            {new Date(trade.created_at).toLocaleString()}
          </span>
        </div>
      </div>

      {/* Key metrics grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 12, marginBottom: 16 }}>
        {[
          ['Est. Value', price(trade.est_value, { from: 'USD', digits: 0 }).text],
          ['Risk Amount', price(trade.risk_amount, { from: 'USD' }).text],
          ['Signal Score', `${trade.signal_score > 0 ? '+' : ''}${Number(trade.signal_score).toFixed(1)}`],
          ['Confidence', trade.confidence || '—'],
          ['Stop Loss', trade.stop_loss ? `$${trade.stop_loss}` : '—'],
          ['Take Profit', trade.take_profit ? `$${trade.take_profit}` : '—'],
          ['R:R Ratio', riskRatio ? `1 : ${riskRatio}` : '—'],
          ['Broker', (trade.broker || 'auto').toUpperCase()],
        ].map(([label, val]) => (
          <div key={label} style={{ background: 'var(--inset)', borderRadius: 10, padding: '8px 12px' }}>
            <div style={{ fontFamily: 'var(--font)', fontSize: 12, color: 'var(--ink-2)', marginBottom: 3 }}>{label}</div>
            <div style={{ fontFamily: 'var(--font)', fontSize: 14, color: 'var(--ink)', fontWeight: 600 }}>{val}</div>
          </div>
        ))}
      </div>

      {/* Situation archetype */}
      {trade.situation && (
        <div style={{ marginBottom: 12 }}>
          <span style={{ fontFamily: 'var(--font)', fontSize: 12, color: 'var(--ink-2)' }}>Situation: </span>
          <span style={{ fontFamily: 'var(--font)', fontSize: 13, color: 'var(--accent)', fontWeight: 600 }}>
            {trade.situation.replace(/_/g, ' ')}
          </span>
        </div>
      )}

      {/* Thesis */}
      {(trade.bull_case || trade.bear_case || trade.invalidation) && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginBottom: 16 }}>
          {trade.bull_case && (
            <div style={{ background: 'color-mix(in srgb, var(--green) 6%, transparent)', border: '1px solid color-mix(in srgb, var(--green) 20%, transparent)', borderRadius: 10, padding: '8px 12px' }}>
              <div style={{ fontSize: 12, color: 'var(--green)', fontFamily: 'var(--font)', marginBottom: 4 }}>▲ Bull case</div>
              <div style={{ fontSize: 13, color: 'var(--ink)' }}>{trade.bull_case}</div>
            </div>
          )}
          {trade.bear_case && (
            <div style={{ background: 'color-mix(in srgb, var(--red) 6%, transparent)', border: '1px solid color-mix(in srgb, var(--red) 20%, transparent)', borderRadius: 10, padding: '8px 12px' }}>
              <div style={{ fontSize: 12, color: 'var(--red)', fontFamily: 'var(--font)', marginBottom: 4 }}>▼ Bear case</div>
              <div style={{ fontSize: 13, color: 'var(--ink)' }}>{trade.bear_case}</div>
            </div>
          )}
          {trade.invalidation && (
            <div style={{ background: 'color-mix(in srgb, var(--yellow) 6%, transparent)', border: '1px solid color-mix(in srgb, var(--yellow) 20%, transparent)', borderRadius: 10, padding: '8px 12px' }}>
              <div style={{ fontSize: 12, color: 'var(--yellow)', fontFamily: 'var(--font)', marginBottom: 4 }}>! Invalidation</div>
              <div style={{ fontSize: 13, color: 'var(--ink)' }}>{trade.invalidation}</div>
            </div>
          )}
        </div>
      )}

      {/* Executed info */}
      {trade.status === 'executed' && (
        <div style={{ fontFamily: 'var(--font)', fontSize: 14, color: 'var(--green)', marginBottom: 12 }}>
          ✅ Filled @ ${trade.fill_price} · Order ID: {trade.broker_order_id} · {new Date(trade.executed_at).toLocaleString()}
        </div>
      )}
      {trade.status === 'rejected' && trade.rejection_reason && (
        <div style={{ fontFamily: 'var(--font)', fontSize: 14, color: 'var(--red)', marginBottom: 12 }}>
          ✗ Rejected: {trade.rejection_reason}
        </div>
      )}

      {/* Error */}
      {error && (
        <div style={{ fontFamily: 'var(--font)', fontSize: 14, color: 'var(--red)', marginBottom: 10, padding: '6px 10px', background: 'color-mix(in srgb, var(--red) 10%, transparent)', borderRadius: 10 }}>
          ⚠ {error}
        </div>
      )}

      {/* Action buttons — only for pending */}
      {isPending && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            onClick={() => handle('approve')}
            disabled={loading}
            style={{
              background: loading ? 'var(--inset)' : 'var(--green)',
              color: 'var(--on-solid)', border: 'none', borderRadius: 10,
              padding: '10px 28px', fontFamily: 'var(--font)',
              fontSize: 14, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer',
              
              transition: 'all 0.15s',
            }}
          >
            {loading ? 'Executing...' : '✓ Approve & execute'}
          </button>

          {!showReject ? (
            <button
              onClick={() => setShowReject(true)}
              disabled={loading}
              style={{
                background: 'transparent', color: 'var(--red)',
                border: '1px solid var(--red)', borderRadius: 10,
                padding: '10px 20px', fontFamily: 'var(--font)',
                fontSize: 14, fontWeight: 600, cursor: 'pointer', 
              }}
            >
              ✗ Reject
            </button>
          ) : (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input
                value={rejectReason}
                onChange={e => setRejectReason(e.target.value)}
                placeholder="Reason (optional)"
                style={{
                  background: 'var(--inset)', border: '1px solid var(--red)',
                  color: 'var(--ink)', padding: '8px 12px', borderRadius: 10,
                  fontFamily: 'var(--font)', fontSize: 14, width: 220,
                }}
              />
              <button onClick={() => handle('reject')} disabled={loading} style={{
                background: 'var(--red)', color: 'var(--ink)', border: 'none',
                borderRadius: 10, padding: '9px 16px', fontFamily: 'var(--font)',
                fontSize: 14, fontWeight: 600, cursor: 'pointer',
              }}>Confirm reject</button>
              <button onClick={() => setShowReject(false)} style={{
                background: 'transparent', color: 'var(--ink-2)', border: 'none',
                cursor: 'pointer', fontFamily: 'var(--font)', fontSize: 14,
              }}>Cancel</button>
            </div>
          )}

          <button
            onClick={() => handle('cancel')}
            disabled={loading}
            style={{
              background: 'transparent', color: 'var(--ink-2)',
              border: '1px solid var(--line)', borderRadius: 10,
              padding: '10px 16px', fontFamily: 'var(--font)',
              fontSize: 14, cursor: 'pointer',
            }}
          >
            — Cancel
          </button>
        </div>
      )}
    </div>
  )
}

// ─── Stats bar ────────────────────────────────────────────────────────────────
function StatsBar({ stats }) {
  if (!stats) return null
  const items = [
    ['PENDING', stats.pending || 0, 'var(--yellow)'],
    ['EXECUTED', stats.executed || 0, 'var(--green)'],
    ['REJECTED', stats.rejected || 0, 'var(--red)'],
    ['CANCELLED', stats.cancelled || 0, 'var(--ink-2)'],
    ['TOTAL', stats.total || 0, 'var(--accent)'],
  ]
  return (
    <div style={{ display: 'flex', gap: 2, marginBottom: 20 }}>
      {items.map(([label, val, color]) => (
        <div key={label} style={{
          flex: 1, background: 'var(--inset)', border: '1px solid var(--line)',
          borderRadius: 10, padding: '10px 14px', textAlign: 'center',
        }}>
          <div style={{ fontFamily: 'var(--font)', fontSize: 26, fontWeight: 600, color }}>{val}</div>
          <div style={{ fontFamily: 'var(--font)', fontSize: 12, color: 'var(--ink-2)', marginTop: 2 }}>{label}</div>
        </div>
      ))}
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────
export default function Execution() {
  const [filter, setFilter] = useState('pending')
  const { data, loading, refetch } = useApprovalQueue(3000)

  const trades = data?.trades || []
  const filtered = filter === 'all' ? trades : trades.filter(t => t.status === filter)
  const pendingCount = trades.filter(t => t.status === 'pending').length

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto' }}>
      {/* Page header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontFamily: 'var(--font)', fontSize: 26, fontWeight: 600, color: 'var(--accent)', margin: 0 }}>
            Trade execution
          </h1>
          <p style={{ fontFamily: 'var(--font)', fontSize: 13, color: 'var(--ink-2)', margin: '4px 0 0' }}>
            ARIA proposes · you approve · broker executes
          </p>
        </div>
        {pendingCount > 0 && (
          <div style={{
            background: 'color-mix(in srgb, var(--yellow) 15%, transparent)', border: '1px solid var(--yellow)',
            borderRadius: 12, padding: '8px 16px', fontFamily: 'var(--font)',
            fontSize: 14, color: 'var(--yellow)', fontWeight: 600,
            animation: 'pulse 1.5s ease-in-out infinite',
          }}>
            ⏳ {pendingCount} trade{pendingCount > 1 ? 'S' : ''} awaiting approval
          </div>
        )}
      </div>

      {/* Broker status */}
      <BrokerPanel />

      {/* Live positions */}
      <PositionsTable />

      {/* Stats */}
      <StatsBar stats={data?.stats} />

      {/* Filter tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 20 }}>
        {[
          ['pending', 'PENDING', pendingCount],
          ['executed', 'EXECUTED', null],
          ['rejected', 'REJECTED', null],
          ['cancelled', 'CANCELLED', null],
          ['all', 'ALL', trades.length],
        ].map(([val, label, badge]) => (
          <button
            key={val}
            onClick={() => setFilter(val)}
            style={{
              background: filter === val ? 'var(--accent)' : 'transparent',
              color: filter === val ? 'var(--on-solid)' : 'var(--ink-2)',
              border: `1px solid ${filter === val ? 'var(--accent)' : 'var(--line)'}`,
              borderRadius: 10, padding: '6px 14px',
              fontFamily: 'var(--font)', fontSize: 13, fontWeight: 600,
              cursor: 'pointer', 
              display: 'flex', alignItems: 'center', gap: 6,
            }}
          >
            {label}
            {badge != null && (
              <span style={{
                background: filter === val ? 'var(--inset)' : 'var(--inset)',
                borderRadius: 16, padding: '1px 7px', fontSize: 12,
              }}>{badge}</span>
            )}
          </button>
        ))}
      </div>

      {/* Trade cards */}
      {loading ? (
        <div style={{ fontFamily: 'var(--font)', color: 'var(--ink-2)', padding: 40, textAlign: 'center' }}>
          Loading queue...
        </div>
      ) : filtered.length === 0 ? (
        <div style={{
          fontFamily: 'var(--font)', color: 'var(--ink-2)', padding: 60,
          textAlign: 'center', border: '1px dashed var(--line)', borderRadius: 14,
        }}>
          <div style={{ fontSize: 36, marginBottom: 12 }}>📋</div>
          <div style={{ fontSize: 15 }}>No {filter === 'all' ? '' : filter} trades</div>
          {filter === 'pending' && (
            <div style={{ fontSize: 13, marginTop: 8 }}>
              ARIA will propose trades here when it detects high-conviction signals.
            </div>
          )}
        </div>
      ) : (
        filtered.map(trade => (
          <TradeCard key={trade.id} trade={trade} onAction={refetch} />
        ))
      )}

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.6; }
        }
      `}</style>
    </div>
  )
}
