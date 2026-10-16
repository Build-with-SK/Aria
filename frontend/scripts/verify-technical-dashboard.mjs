// Offline render and consultation-boundary smoke tests. No HTTP server or keys.
import { build } from 'rolldown'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { MemoryRouter } from 'react-router-dom'
import { technicalEvidence, isExactGoldContract } from '../src/components/technicalEvidence.js'
import { exactResearchSelection, analysisRequestPlan } from '../src/components/researchSelection.js'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const temporary = mkdtempSync(join(root, 'node_modules', '.technical-smoke-'))
try {
  await build({ input: join(root, 'src/components/TechnicalDashboard.jsx'), platform: 'node',
    external: ['react', 'react/jsx-runtime', 'axios'], output: { file: join(temporary, 'dashboard.mjs'), format: 'esm' } })
  const { default: Dashboard } = await import(pathToFileURL(join(temporary, 'dashboard.mjs')).href)
  writeFileSync(join(temporary, 'entry.jsx'), `export { default } from ${JSON.stringify(join(root, 'src/pages/Research.jsx').replaceAll('\\', '/'))}; export { CurrencyProvider } from ${JSON.stringify(join(root, 'src/currency/CurrencyContext.jsx').replaceAll('\\', '/'))}; export { default as ResearchAnalysis } from ${JSON.stringify(join(root, 'src/components/ResearchAnalysis.jsx').replaceAll('\\', '/'))};`)
  await build({ input: join(temporary, 'entry.jsx'), platform: 'node',
    external: ['react', 'react/jsx-runtime', 'react-dom', 'react-router-dom', 'axios', 'recharts', 'lucide-react'],
    output: { file: join(temporary, 'research.mjs'), format: 'esm' } })
  const { default: Research, CurrencyProvider, ResearchAnalysis } = await import(pathToFileURL(join(temporary, 'research.mjs')).href)
  const engine = renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(ResearchAnalysis, { symbol: 'AAPL', includeTechnical: false })))
  assert.ok(engine.includes('ARIA ENGINE ANALYSIS') && engine.includes('RUN ENGINE ANALYSIS ON AAPL'))
  assert.ok(!engine.includes('TECHNICAL ANALYSIS'), 'embedded engine must not duplicate the shared technical panel')
  for (const alias of ['GCZ6', 'gcz26', 'GCZ2026', 'GCZ26.CMX']) {
    assert.ok(isExactGoldContract(alias))
    const research = renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: [`/research?symbol=${alias}`] }, React.createElement(Research)))
    assert.ok(research.includes(`RUN TECHNICAL ANALYSIS ON ${alias.toUpperCase()}`), 'exact contract must reach technical analysis directly')
    assert.ok(!research.includes('FULL ENSEMBLE') && !research.includes('RESEARCH THESIS'), 'equity paths must not substitute the contract')
  }
  for (const other of ['GC=F', 'AAPL', 'GCZ6.CMX', 'GCZ27']) assert.ok(!isExactGoldContract(other))
  for (const symbol of ['SAIL.BO', 'RELIANCE', 'AAPL', 'HSBA', 'BTC-USD', 'EURUSD=X', 'SPY', '^NSEI', 'GC=F', 'TLT', 'HYG']) {
    const html = renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: [`/research?symbol=${encodeURIComponent(symbol)}`] }, React.createElement(CurrencyProvider, null, React.createElement(Research))))
    assert.equal((html.match(/RUN TECHNICAL ANALYSIS ON/g) || []).length, 1, `${symbol}: exactly one shared technical panel`)
    assert.ok(html.indexOf('RUN TECHNICAL ANALYSIS ON') < html.indexOf(`OVERVIEW`), `${symbol}: analysis before chart`)
  }
  assert.equal(exactResearchSelection([{ symbol: 'SAIL', yahoo: 'SAIL' }], 'SAIL.BO'), null)
  assert.equal(exactResearchSelection([{ symbol: 'SAIL.BO' }], 'sail.bo').symbol, 'SAIL.BO')
  assert.equal(exactResearchSelection([{ symbol: 'A', yahoo: 'A.L' }, { symbol: 'ALIAS', yahoo: 'A.L' }], 'A.L'), null)
  assert.deepEqual(analysisRequestPlan({ technicalOnly: true }), { technical: true, engine: false })
  assert.deepEqual(analysisRequestPlan({ includeTechnical: false }), { technical: false, engine: true })
  assert.deepEqual(analysisRequestPlan({ exactContract: true }), { technical: true, engine: false })
  const frames = ['1m','5m','15m','30m','1h','5h','1d','1w','1M']
  const report = { assessment: 'Timeframe conflict — inspect both horizons.', consensus: null,
    conflicts: [{ warning: '1h Strong Buy conflicts with 1d Strong Sell; no unconditional direction.' }],
    timeframes: Object.fromEntries(frames.map(tf => [tf, { summary: tf === '1d' ? 'Strong Sell' : 'Strong Buy',
      instrument: { provider_symbol: 'SYNTHETIC', exchange: 'TEST' }, data_as_of: '2026-10-15T00:00:00Z',
      evidence: { quality: 'synthetic fixture', bars: 260 }, data_quality: 'synthetic fixture',
      indicator_values: Object.fromEntries(['RSI14','Stochastic14','StochRSI14','MACD12_26_9','ADX14','WilliamsR14','CCI20','ROC12','IchimokuCloud'].map(name => [name, { value: 55, signal: 'buy' }])),
      moving_average_values: Object.fromEntries(['SMA20','SMA50','SMA100','SMA200','EMA9','EMA21','EMA50','EMA200'].map(name => [name,{ value: 100, signal: 'buy' }])),
    }])) }
  const html = renderToStaticMarkup(React.createElement(Dashboard, { symbol: 'SYNTHETIC', tech: report }))
  assert.equal((html.match(/aria-pressed=/g) || []).length, 9)
  assert.ok(html.includes('Strong Buy') && html.includes('Strong Sell'))
  assert.ok(html.includes('No independent review obtained. Silence is not agreement.'))
  assert.ok(!html.includes('NaN'))
  const context = technicalEvidence('SYNTHETIC', report.timeframes)
  assert.ok(encodeURIComponent(context).length < 14000, 'all nine horizons must fit the existing consultation interface')
  assert.equal(Object.keys(JSON.parse(context).timeframes).length, 9)
  assert.ok(!context.includes('Strong Buy'), 'withhold ARIA directional labels from independent reconstruction')
  const missing = renderToStaticMarkup(React.createElement(Dashboard, { symbol: 'GCZ6', tech: { timeframes: { '1d': { error: 'contract unavailable' } } } }))
  assert.ok(missing.includes('contract unavailable') && missing.includes('disabled=""'))
  console.log(`Dashboard checks passed: nine horizons, conflicts, unavailable contract, review provenance; encoded evidence ${encodeURIComponent(context).length} bytes.`)
} finally {
  // Only this freshly created child of node_modules is removed.
  assert.ok(temporary.startsWith(join(root, 'node_modules', '.technical-smoke-')))
  rmSync(temporary, { recursive: true, force: true })
}
