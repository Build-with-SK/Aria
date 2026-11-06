// Offline SSR only: effects do not run, no real account or HTTP server.
import { build } from 'rolldown'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const temporary = mkdtempSync(join(root, 'node_modules', '.multiasset-render-'))
try {
  await build({ input: join(root, 'src/components/MultiassetWorkspace.jsx'), platform: 'node',
    external: ['react', 'react/jsx-runtime', 'axios'],
    output: { file: join(temporary, 'workspace.mjs'), format: 'esm' } })
  const { MultiassetView } = await import(pathToFileURL(join(temporary, 'workspace.mjs')).href)
  const render = props => renderToStaticMarkup(React.createElement(MultiassetView, props))
  const loading = render({ loading: true })
  assert.ok(loading.includes('Loading multiasset'))
  for (const props of [{}, { error: true }, { data: { schema_version: 'unsupported' } }]) {
    const html = render(props)
    assert.ok(html.includes('state unavailable'))
    assert.ok(!html.includes('EXECUTION DISABLED'), 'unknown server state must remain unavailable')
  }
  const data = { schema_version: '1', execution_mode: 'DISABLED', account: null,
    rule_profile: { status: 'POLICY_UNVERIFIED' }, mt5: { status: 'NOT_CONFIGURED' },
    data_state: 'DISCONNECTED', decision: { status: 'ABSTAIN', reason: 'Fixture: no verified inputs' },
    research_universe: [{ asset_class: 'Forex', features: ['technical', 'macro'], broker_tradability: 'UNVERIFIED' }],
    strategies: [{ family: 'TrendFollowing' }], as_of: '2026-11-06T12:00:00Z' }
  const html = render({ data })
  assert.ok(html.includes('NO TRADE') && html.includes('POLICY_UNVERIFIED'))
  assert.ok(html.includes('DISCONNECTED') && html.includes('Unavailable'))
  assert.ok(!html.includes('<button') && !html.includes('EXECUTED'))
  assert.ok(!html.includes('NaN') && !html.includes('$10,000'))
  assert.ok(render({ data: { ...data, equity: '0' } }).includes('>0</div>'), 'real zero differs from unknown')
  console.log('PASS: multiasset loading, disconnected, unavailable, empty, unknown-schema and zero-value renders; no order controls')
} finally {
  rmSync(temporary, { recursive: true, force: true })
}
