import React from 'react'

export function executionLabel(trade) {
  if (trade.status === 'unknown') return 'UNKNOWN — CHECK BROKER'
  if (trade.status === 'submitting') return 'SUBMITTING — UNCONFIRMED'
  if (trade.status === 'executed') {
    return ({ filled: 'FILLED', partial: 'PARTIALLY FILLED', submitted: 'WORKING — FILL UNCONFIRMED',
      cancelled: 'CANCELLED AT BROKER', rejected: 'REJECTED AT BROKER', expired: 'EXPIRED AT BROKER' })[trade.order_status]
      || 'ACKNOWLEDGED — FILL UNCONFIRMED'
  }
  return ({ pending: 'PENDING APPROVAL', approved: 'APPROVED — UNSENT', rejected: 'REJECTED',
    cancelled: 'CANCELLED' })[trade.status] || 'STATUS UNAVAILABLE'
}

export function StatusPill({ trade }) {
  const confirmed = trade.status === 'executed' && trade.order_status === 'filled'
  return <span style={{ fontFamily: 'var(--mono)', fontSize: 11, fontWeight: 700,
    color: confirmed ? 'var(--green)' : 'var(--orange)', border: '1px solid var(--border)',
    borderRadius: 3, padding: '2px 8px', letterSpacing: 1 }}>{executionLabel(trade)}</span>
}

export function ExecutionReceipt({ trade }) {
  if (!['executed', 'unknown', 'submitting'].includes(trade.status)) return null
  const filled = Number(trade.filled_qty) > 0
  const price = Number(trade.fill_price)
  return <div style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--text-dim)', marginBottom: 12 }}>
    {executionLabel(trade)}
    {filled && <span> · Confirmed quantity: {trade.filled_qty}
      {Number.isFinite(price) && price > 0 ? ` @ $${price}` : ' · Fill price unavailable'}</span>}
    {trade.broker_order_id && <span> · Order ID: {trade.broker_order_id}</span>}
    {trade.client_order_id && <div>Client order ID: {trade.client_order_id}</div>}
    {['unknown', 'submitting'].includes(trade.status) &&
      <div>The broker may hold this order. Verify its state before another submission.</div>}
  </div>
}
