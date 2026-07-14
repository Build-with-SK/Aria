import React, { useState } from 'react'
import { BBCard, SectionHeader, ScoreBadge, ActionBadge } from '../components/UI'
import {
  useApprovalQueue, useBrokerStatus, useLivePositions,
  approveAndExecute, rejectTrade, cancelTrade
} from '../hooks/useExecution'

// ─── Status pill ─────────────────────────────────────────────────────────────
function StatusPill({ status }) {
  const cfg = {
    pending:   { color: '#ffcc00', bg: 'rgba(255,204,0,0.12)',   label: '⏳ PENDING APPROVAL' },
    approved:  { color: '#00aaff', bg: 'rgba(0,170,255,0.12)',  label: '✓ APPROVED' },
    executed:  { color: '#00cc44', bg: 'rgba(0,204,68,0.12)',   label: '✅ EXECUTED' },
    rejected:  { color: '#ff3333', bg: 'rgba(255,51,51,0.12)',  label: '✗ REJECTED' },
    cancelled: { color: '#888',    bg: 'rgba(136,136,136,0.1)', label: '— CANCELLED' },
  }
  const c = cfg[status] || cfg.cancelled
  return (
    <span style={{
      fontFamily: 'var(--mono)', fontSize: 11, fontWeight: 700,
      color: c.color, background: c.bg,
      border: `1px solid ${c.color}44`,
      borderRadius: 3, padding: '2px 8px', letterSpacing: 1,
    }}>{c.label}</span>
  )
}

// ─── Broker status panel ──────────────────────────────────────────────────────
function BrokerPanel() {
  const { data, loading } = useBrokerStatus()

  const BrokerCard = ({ name, info }) => (
    <div style={{
      background: '#0a0a0a', border: `1px solid ${info?.connected ? 'var(--green)' : 'var(--border)'}`,
      borderRadius: 6, padding: '14px 18px', flex: 1,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--orange)', fontWeight: 700 }}>
          {name}
        </span>
        <span style={{
          fontSize: 11, fontFamily: 'var(--mono)', fontWeight: 700,
          color: info?.connected ? 'var(--green)' : '#ff3333',
        }}>
          {info?.connected ? '● LIVE' : '○ OFFLINE'}
          {info?.paper ? ' (PAPER)' : ''}
        </span>
      </div>
      {info?.account ? (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {[
            ['Cash', `$${Number(info.account.cash).toLocaleString()}`],
            ['Portfolio', `$${Number(info.account.portfolio_value).toLocaleString()}`],
            ['Buying Power', `$${Number(info.account.buying_power).toLocaleString()}`],
          ].map(([label, val]) => (
            <div key={label}>
              <div style={{ color: 'var(--text-dim)', fontSize: 10, fontFamily: 'var(--mono)' }}>{label}</div>
              <div style={{ color: '#fff', fontSize: 14, fontFamily: 'var(--mono)', fontWeight: 600 }}>{val}</div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: 'var(--text-dim)', fontSize: 12, fontFamily: 'var(--mono)' }}>
          {info?.connected ? 'Loading account...' : 'Not connected. Check API keys in .env'}
        </div>
      )}
    </div>
  )

  return (
    <div style={{ display: 'flex', gap: 16, marginBottom: 24 }}>
      <BrokerCard name="ALPACA" info={loading ? null : data?.alpaca} />
      <BrokerCard name="IBKR"   info={loading ? null : data?.ibkr}   />
    </div>
  )
}

// ─── Live positions table ─────────────────────────────────────────────────────
function PositionsTable() {
  const data = useLivePositions()
  if (!data?.positions?.length) return null

  return (
    <BBCard title="LIVE POSITIONS" style={{ marginBottom: 24 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--mono)', fontSize: 12 }}>
        <thead>
          <tr style={{ color: 'var(--text-dim)', textAlign: 'left' }}>
            {['Ticker', 'Side', 'Qty', 'Avg Cost', 'Market Value', 'Unrealized P&L', 'Broker'].map(h => (
              <th key={h} style={{ padding: '6px 10px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 10 }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.positions.map((p, i) => (
            <tr key={i} style={{ borderBottom: '1px solid #111' }}>
              <td style={{ padding: '8px 10px', color: 'var(--orange)', fontWeight: 700 }}>{p.ticker}</td>
              <td style={{ padding: '8px 10px', color: p.side === 'long' ? 'var(--green)' : 'var(--red)', textTransform: 'uppercase' }}>{p.side}</td>
              <td style={{ padding: '8px 10px', color: '#fff' }}>{p.qty}</td>
              <td style={{ padding: '8px 10px', color: '#fff' }}>${Number(p.avg_cost).toFixed(2)}</td>
              <td style={{ padding: '8px 10px', color: '#fff' }}>${Number(p.market_value).toLocaleString()}</td>
              <td style={{ padding: '8px 10px', color: p.unrealized_pl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 700 }}>
                {p.unrealized_pl >= 0 ? '+' : ''}${Number(p.unrealized_pl).toFixed(2)}
              </td>
              <td style={{ padding: '8px 10px', color: 'var(--text-dim)' }}>{p.broker}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </BBCard>
  )
}

// ─── Single trade card ────────────────────────────────────────────────────────
function TradeCard({ trade, onAction }) {
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
      background: '#070707',
      border: `1px solid ${isPending ? 'var(--orange)' : 'var(--border)'}`,
      borderLeft: `4px solid ${isPending ? 'var(--orange)' : trade.status === 'executed' ? 'var(--green)' : trade.status === 'rejected' ? 'var(--red)' : 'var(--border)'}`,
      borderRadius: 6, padding: 20, marginBottom: 16,
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 22, fontWeight: 800, color: 'var(--orange)' }}>
            {trade.ticker}
          </span>
          <span style={{
            fontFamily: 'var(--mono)', fontSize: 16, fontWeight: 800, color: sideColor,
            textTransform: 'uppercase', letterSpacing: 2,
          }}>
            {trade.side === 'buy' ? '▲ BUY' : '▼ SELL'}
          </span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 14, color: '#fff' }}>
            {trade.qty} {trade.asset_class === 'crypto' ? 'units' : 'shares'}
          </span>
          {trade.order_type !== 'market' && (
            <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-dim)', textTransform: 'uppercase' }}>
              [{trade.order_type}]
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <StatusPill status={trade.status} />
          <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--text-dim)' }}>
            {new Date(trade.created_at).toLocaleString()}
          </span>
        </div>
      </div>

      {/* Key metrics grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 12, marginBottom: 16 }}>
        {[
          ['Est. Value', `$${Number(trade.est_value).toLocaleString()}`],
          ['Risk Amount', `$${Number(trade.risk_amount).toFixed(2)}`],
          ['Signal Score', `${trade.signal_score > 0 ? '+' : ''}${Number(trade.signal_score).toFixed(1)}`],
          ['Confidence', trade.confidence || '—'],
          ['Stop Loss', trade.stop_loss ? `$${trade.stop_loss}` : '—'],
          ['Take Profit', trade.take_profit ? `$${trade.take_profit}` : '—'],
          ['R:R Ratio', riskRatio ? `1 : ${riskRatio}` : '—'],
          ['Broker', (trade.broker || 'auto').toUpperCase()],
        ].map(([label, val]) => (
          <div key={label} style={{ background: '#0d0d0d', borderRadius: 4, padding: '8px 12px' }}>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--text-dim)', marginBottom: 3 }}>{label}</div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 13, color: '#fff', fontWeight: 600 }}>{val}</div>
          </div>
        ))}
      </div>

      {/* Situation archetype */}
      {trade.situation && (
        <div style={{ marginBottom: 12 }}>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--text-dim)' }}>SITUATION: </span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--orange)', fontWeight: 700 }}>
            {trade.situation.replace(/_/g, ' ')}
          </span>
        </div>
      )}

      {/* Thesis */}
      {(trade.bull_case || trade.bear_case || trade.invalidation) && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginBottom: 16 }}>
          {trade.bull_case && (
            <div style={{ background: 'rgba(0,204,68,0.06)', border: '1px solid rgba(0,204,68,0.2)', borderRadius: 4, padding: '8px 12px' }}>
              <div style={{ fontSize: 10, color: 'var(--green)', fontFamily: 'var(--mono)', marginBottom: 4 }}>▲ BULL CASE</div>
              <div style={{ fontSize: 11, color: '#ccc' }}>{trade.bull_case}</div>
            </div>
          )}
          {trade.bear_case && (
            <div style={{ background: 'rgba(255,51,51,0.06)', border: '1px solid rgba(255,51,51,0.2)', borderRadius: 4, padding: '8px 12px' }}>
              <div style={{ fontSize: 10, color: 'var(--red)', fontFamily: 'var(--mono)', marginBottom: 4 }}>▼ BEAR CASE</div>
              <div style={{ fontSize: 11, color: '#ccc' }}>{trade.bear_case}</div>
            </div>
          )}
          {trade.invalidation && (
            <div style={{ background: 'rgba(255,204,0,0.06)', border: '1px solid rgba(255,204,0,0.2)', borderRadius: 4, padding: '8px 12px' }}>
              <div style={{ fontSize: 10, color: 'var(--yellow)', fontFamily: 'var(--mono)', marginBottom: 4 }}>! INVALIDATION</div>
              <div style={{ fontSize: 11, color: '#ccc' }}>{trade.invalidation}</div>
            </div>
          )}
        </div>
      )}

      {/* Executed info */}
      {trade.status === 'executed' && (
        <div style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--green)', marginBottom: 12 }}>
          ✅ Filled @ ${trade.fill_price} · Order ID: {trade.broker_order_id} · {new Date(trade.executed_at).toLocaleString()}
        </div>
      )}
      {trade.status === 'rejected' && trade.rejection_reason && (
        <div style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--red)', marginBottom: 12 }}>
          ✗ Rejected: {trade.rejection_reason}
        </div>
      )}

      {/* Error */}
      {error && (
        <div style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--red)', marginBottom: 10, padding: '6px 10px', background: 'rgba(255,51,51,0.1)', borderRadius: 4 }}>
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
              background: loading ? '#1a1a1a' : 'var(--green)',
              color: '#000', border: 'none', borderRadius: 4,
              padding: '10px 28px', fontFamily: 'var(--mono)',
              fontSize: 13, fontWeight: 800, cursor: loading ? 'not-allowed' : 'pointer',
              letterSpacing: 1, textTransform: 'uppercase',
              boxShadow: loading ? 'none' : '0 0 16px rgba(0,204,68,0.4)',
              transition: 'all 0.15s',
            }}
          >
            {loading ? 'EXECUTING...' : '✓ APPROVE & EXECUTE'}
          </button>

          {!showReject ? (
            <button
              onClick={() => setShowReject(true)}
              disabled={loading}
              style={{
                background: 'transparent', color: 'var(--red)',
                border: '1px solid var(--red)', borderRadius: 4,
                padding: '10px 20px', fontFamily: 'var(--mono)',
                fontSize: 13, fontWeight: 700, cursor: 'pointer', letterSpacing: 1,
              }}
            >
              ✗ REJECT
            </button>
          ) : (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input
                value={rejectReason}
                onChange={e => setRejectReason(e.target.value)}
                placeholder="Reason (optional)"
                style={{
                  background: '#111', border: '1px solid var(--red)',
                  color: '#fff', padding: '8px 12px', borderRadius: 4,
                  fontFamily: 'var(--mono)', fontSize: 12, width: 220,
                }}
              />
              <button onClick={() => handle('reject')} disabled={loading} style={{
                background: 'var(--red)', color: '#fff', border: 'none',
                borderRadius: 4, padding: '9px 16px', fontFamily: 'var(--mono)',
                fontSize: 12, fontWeight: 700, cursor: 'pointer',
              }}>CONFIRM REJECT</button>
              <button onClick={() => setShowReject(false)} style={{
                background: 'transparent', color: 'var(--text-dim)', border: 'none',
                cursor: 'pointer', fontFamily: 'var(--mono)', fontSize: 12,
              }}>Cancel</button>
            </div>
          )}

          <button
            onClick={() => handle('cancel')}
            disabled={loading}
            style={{
              background: 'transparent', color: 'var(--text-dim)',
              border: '1px solid #333', borderRadius: 4,
              padding: '10px 16px', fontFamily: 'var(--mono)',
              fontSize: 12, cursor: 'pointer',
            }}
          >
            — CANCEL
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
    ['CANCELLED', stats.cancelled || 0, 'var(--text-dim)'],
    ['TOTAL', stats.total || 0, 'var(--orange)'],
  ]
  return (
    <div style={{ display: 'flex', gap: 2, marginBottom: 20 }}>
      {items.map(([label, val, color]) => (
        <div key={label} style={{
          flex: 1, background: '#0a0a0a', border: '1px solid var(--border)',
          borderRadius: 4, padding: '10px 14px', textAlign: 'center',
        }}>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 22, fontWeight: 800, color }}>{val}</div>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--text-dim)', marginTop: 2 }}>{label}</div>
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
          <h1 style={{ fontFamily: 'var(--mono)', fontSize: 22, fontWeight: 800, color: 'var(--orange)', margin: 0 }}>
            TRADE EXECUTION
          </h1>
          <p style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-dim)', margin: '4px 0 0' }}>
            ARIA proposes · you approve · broker executes
          </p>
        </div>
        {pendingCount > 0 && (
          <div style={{
            background: 'rgba(255,204,0,0.15)', border: '1px solid var(--yellow)',
            borderRadius: 6, padding: '8px 16px', fontFamily: 'var(--mono)',
            fontSize: 13, color: 'var(--yellow)', fontWeight: 700,
            animation: 'pulse 1.5s ease-in-out infinite',
          }}>
            ⏳ {pendingCount} TRADE{pendingCount > 1 ? 'S' : ''} AWAITING APPROVAL
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
              background: filter === val ? 'var(--orange)' : 'transparent',
              color: filter === val ? '#000' : 'var(--text-dim)',
              border: `1px solid ${filter === val ? 'var(--orange)' : 'var(--border)'}`,
              borderRadius: 4, padding: '6px 14px',
              fontFamily: 'var(--mono)', fontSize: 11, fontWeight: 700,
              cursor: 'pointer', letterSpacing: 1,
              display: 'flex', alignItems: 'center', gap: 6,
            }}
          >
            {label}
            {badge != null && (
              <span style={{
                background: filter === val ? 'rgba(0,0,0,0.25)' : 'rgba(255,255,255,0.1)',
                borderRadius: 10, padding: '1px 7px', fontSize: 10,
              }}>{badge}</span>
            )}
          </button>
        ))}
      </div>

      {/* Trade cards */}
      {loading ? (
        <div style={{ fontFamily: 'var(--mono)', color: 'var(--text-dim)', padding: 40, textAlign: 'center' }}>
          LOADING QUEUE...
        </div>
      ) : filtered.length === 0 ? (
        <div style={{
          fontFamily: 'var(--mono)', color: 'var(--text-dim)', padding: 60,
          textAlign: 'center', border: '1px dashed var(--border)', borderRadius: 8,
        }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>📋</div>
          <div style={{ fontSize: 14 }}>No {filter === 'all' ? '' : filter} trades</div>
          {filter === 'pending' && (
            <div style={{ fontSize: 11, marginTop: 8 }}>
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
