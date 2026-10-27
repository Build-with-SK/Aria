// Render real execution status components with fixtures; no effects or actions.
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { resolve, join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'rolldown'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const root = resolve(import.meta.dirname, '..')
const temporary = mkdtempSync(join(root, 'node_modules', '.execution-render-'))
try {
  const output = join(temporary, 'status.mjs')
  await build({ input: join(root, 'src/components/ExecutionStatus.jsx'), platform: 'node',
    external: ['react'], output: { file: output, format: 'esm' } })
  const { StatusPill, ExecutionReceipt, QueueNotice } = await import(pathToFileURL(output).href)
  for (const [trade, expected] of [
    [{ status: 'unknown', client_order_id: 'fixture-id' }, 'UNKNOWN'],
    [{ status: 'submitting' }, 'SUBMITTING'],
    [{ status: 'executed', order_status: 'submitted', fill_price: 0 }, 'FILL UNCONFIRMED'],
    [{ status: 'executed', fill_price: 100 }, 'FILL UNCONFIRMED'],
    [{ status: 'executed', order_status: 'partial', filled_qty: 2, fill_price: 101 }, 'PARTIALLY FILLED'],
    [{ status: 'executed', order_status: 'filled', filled_qty: 3, fill_price: 101 }, 'FILLED'],
    [{ status: 'new-unrecognised-state' }, 'STATUS UNAVAILABLE'],
  ]) {
    const markup = renderToStaticMarkup(React.createElement(React.Fragment, null,
      React.createElement(StatusPill, { trade }), React.createElement(ExecutionReceipt, { trade })))
    assert.ok(markup.includes(expected), markup)
    if (!(Number(trade.filled_qty) > 0)) assert.ok(!markup.includes('@ $'), markup)
    if (trade.status === 'unknown') assert.ok(markup.includes('fixture-id') && markup.includes('broker may hold'), markup)
    if (trade.status === 'executed' && trade.order_status === 'partial') assert.ok(markup.includes('Confirmed quantity: 2'), markup)
  }
  for (const [props, expected] of [
    [{ error: '503 unavailable', hasData: false }, 'Do not assume the queue is empty'],
    [{ error: '503 unavailable', hasData: true }, 'approval actions are disabled'],
    [{ unresolved: 2 }, '2 UNRESOLVED BROKER ORDERS'],
  ]) {
    const markup = renderToStaticMarkup(React.createElement(QueueNotice, props))
    assert.ok(markup.includes('role="alert"') && markup.includes(expected), markup)
  }
  console.log('Execution status SSR: 7 states passed; unknowns and acknowledgements never rendered as fills.')
  console.log('Queue notices: initial failure, stale data and unresolved orders passed.')
} finally {
  rmSync(temporary, { recursive: true, force: true })
}
