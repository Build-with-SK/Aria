import React from 'react'

export function QueueNotice({ error, hasData, unresolved = 0, onRetry, onReview }) {
  const style = { fontFamily: 'var(--mono)', fontSize: 12, border: '1px solid var(--orange)',
    color: 'var(--orange)', borderRadius: 4, padding: 12, marginBottom: 16 }
  const button = { fontFamily: 'var(--mono)', marginLeft: 12, padding: '5px 9px',
    border: '1px solid var(--orange)', background: 'transparent', color: 'var(--orange)', cursor: 'pointer' }
  return <>
    {error && <div role="alert" style={style}>
      Queue unavailable — {hasData ? 'showing the last loaded records; approval actions are disabled.'
        : 'order state could not be read. Do not assume the queue is empty.'}
      <div>{error}</div>
      <button onClick={onRetry} style={button}>RETRY QUEUE</button>
    </div>}
    {unresolved > 0 && <div role="alert" style={style}>
      {unresolved} UNRESOLVED BROKER ORDER{unresolved === 1 ? '' : 'S'} — verify broker state before another submission.
      <button onClick={onReview} style={button}>REVIEW UNRESOLVED</button>
    </div>}
  </>
}

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
