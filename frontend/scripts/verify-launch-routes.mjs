// Offline SSR of the eight workspace pages. Synthetic data only; no server,
// credentials, account files or browser required. Read hooks are replaced at
// bundle time; effects/event handlers (including trading actions) never run.
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { build } from 'rolldown'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const temporary = mkdtempSync(join(root, 'node_modules', '.launch-render-'))
const routes = { brain: 'AriaBrain', research: 'Research', market: 'Market', portfolio: 'PortfolioWorkspace',
  strategies: 'Strategies', 'daily-report': 'DailyReport', 'track-record': 'TrackRecordLedger', system: 'System' }
const populated = { brain: 'Synthetic offline fixture', research: 'OVERVIEW', market: 'Synthetic instrument',
  portfolio: 'Synthetic holding', strategies: 'Synthetic challenger', 'daily-report': 'Synthetic report',
  'track-record': 'claims with a horizon', system: 'Synthetic worker' }
const observed = []
const originalFetch = globalThis.fetch
globalThis.fetch = () => { throw new Error('Offline renders must never issue a request') }
const signal = { ticker: 'TEST', name: 'Synthetic instrument', composite_score: 35, action: 'Buy' }
const world = { market: { breadth: { advancing_pct: 60, bullish: 3, bearish: 2, tracked: 5 },
  regime: { macro_model: 'Synthetic expansion', agree: true }, volatility: { vix: 20 },
  macro: { score: 1 }, leaders: [signal], laggards: [], brain_focus: ['TEST'] },
  system: { activity_24h: { total: 8 } }, unknowns: [], stale_blocks: [], strategies: { catalogued: 1 } }
const realistic = {
  useWorld: world, useSignals: { signals: { TEST: signal } },
  useWorldChanges: { changed: [{ field: 'regime', from: { label: 'old' }, to: { label: 'new' } }], notable_events: [] },
  useRegime: { regime: 'Synthetic expansion', coverage: 0.75, evidence: ['Synthetic observation'], caveats: [] },
  useFutures: { FUT: { name: 'Synthetic future', confirmation_score: 12 } },
  useOptions: { OPT: { avg_call_iv: 0.2, pc_oi_ratio: 1.2, iv_skew: 0.02, final_sentiment_score: -12 } },
  useBrain: { status: 'WAITING', status_reason: 'Synthetic offline fixture', regime: { regime: 'Synthetic expansion', caveats: [] },
    cycle: { count: 2 }, memory: { count: 10, available: false }, llm_available: false, world, cognition: { steps: [] } },
  useBrainActivity: { cognition: { steps: [{ phase: 'REFLECT', activity: 'Synthetic observation', conclusion: 'No measured edge.' }] } },
  useDailyReport: { date: '2026-10-16', generated_at: '2026-10-16T12:00:00Z', executive_summary: 'Synthetic report; no measured edge.',
    macro: { regime: 'Synthetic expansion', vix: { label: 'VIX', value: 20, status: 'OBSERVED' } },
    market: { top_bullish: [{ ...signal, score: 35, provider_symbol: 'TEST', price_unit: 'USD/share' }] },
    news: { items: [{ headline: 'Synthetic headline', source: 'Fixture', evidence_state: 'UNVERIFIED', url: 'https://example.invalid/' }] },
    assessment: { watching: ['Synthetic observation'] }, track_record: {}, blind_spots: ['Synthetic data only'] },
  useReportHistory: { reports: [] }, useAlerts: { alerts: [] },
  useHoldings: { account: { equity: 1000 }, totals: { cash_pct: 90, invested: 100, unrealised_pl: 0, positions: 1 },
    holdings: [{ ticker: 'TEST', name: 'Synthetic holding', qty: 1, avg_cost: 100, last_price: 100, currency: 'USD' }] },
  useRelated: { groups: [] }, usePortfolio: { analysis: {} },
  useLedger: { stats: { total: 1, resolved: 0, pending: 1, by_source: [] }, predictions: [] },
  useLedgerDecisions: { decisions: [] }, useCalibration: { measurable: false, note: 'Calibration withheld: synthetic sample.' },
  useInvestigation: { overall: { rate: null, ci95: null }, distinct_events: 0 },
  useWorkers: { healthy: true, counts: { ok: 1 }, workers: [{ name: 'fixture', label: 'Synthetic worker', loop: 'fast', state: 'ok', ticks: 1, age_seconds: 20 }], degraded: [] },
  useConsultStatus: { sentinel_status: 'UNAVAILABLE', url: 'Synthetic offline fixture', auto_consult_enabled: false, auto_consult_flag: 'OFF' },
  useDataFreshness: { as_of: '2026-10-16T12:00:00', pipeline_run: { state: 'DID_NOT_FINISH', step: 'ml',
    started_at: '2026-10-15T10:00:00', finished_at: null, why: 'Synthetic unfinished run' }, datasets: [
    { dataset: 'signals.json', status: 'FRESH', age_hours: 0, stale_threshold_hours: 30, as_of: '2026-10-16T12:00:00', basis: 'file_mtime', writer: 'Synthetic signals writer',
      observation: { recorded: true, earliest_bar: '2026-10-15', latest_bar: '2026-10-16', rows_dated: 2, rows: 3 } },
    { dataset: 'legacy.json', status: 'STALE', age_hours: 100, stale_threshold_hours: 30, legacy: true, writer: 'Synthetic retired writer' },
    { dataset: 'missing.json', status: 'UNKNOWN', age_hours: null, stale_threshold_hours: 48, as_of: null, writer: null },
  ] },
  '/api/quant/strategies': [{ id: 'synthetic', name: 'Synthetic challenger', metrics: { oos: { sharpe: null }, full: { sharpe: null } } }],
  '/api/evolution/campaigns': { campaigns: [{ started_at: '2026-10-16', generations: 1, population: 2, evaluated: 2, holdout_days: 30, survivors: [], best_in_sample: [] }] },
  '/api/quant/papers': [],
}
try {
  const source = readFileSync(join(root, 'src/hooks/useApi.js'), 'utf8')
  const names = [...source.matchAll(/export (?:async )?(?:const|function) (\w+)/g)].map(m => m[1])
  const mock = join(temporary, 'api.mjs')
  writeFileSync(mock, names.map(name => name.startsWith('use')
    ? `export function ${name}(...args) { const key = ${JSON.stringify(name)} === 'useApiData' ? args[0] : ${JSON.stringify(name)}; globalThis.__launchReads.push(key); return {data: globalThis.__launchFixture[key] ?? globalThis.__launchDefault, loading: !!globalThis.__launchLoading, error: globalThis.__launchError, reload() { throw new Error('No actions during SSR') }} }`
    : `export function ${name}() { throw new Error('No network or actions during SSR: ${name}') }`).join('\n'))
  const entry = join(temporary, 'entry.jsx')
  writeFileSync(entry, Object.entries(routes).map(([route, file]) => `export { default as ${file} } from ${JSON.stringify(join(root, 'src/pages', `${file}.jsx`).replaceAll('\\', '/'))};`).join('\n')
    + `\nexport { ExplorerDetails } from ${JSON.stringify(join(root, 'src/pages/Explorer.jsx').replaceAll('\\', '/'))};`
    + `\nexport { default as DataFreshness } from ${JSON.stringify(join(root, 'src/components/DataFreshness.jsx').replaceAll('\\', '/'))};`
    + `\nexport { CurrencyProvider } from ${JSON.stringify(join(root, 'src/currency/CurrencyContext.jsx').replaceAll('\\', '/'))};`)
  await build({ input: entry, platform: 'node', external: ['react', 'react/jsx-runtime', 'react-dom', 'react-router-dom', 'axios', 'recharts', 'lucide-react'],
    plugins: [{ name: 'offline-api', resolveId(id) { if (/hooks\/useApi(?:\.js)?$/.test(id)) return mock } }],
    output: { file: join(temporary, 'pages.mjs'), format: 'esm' } })
  const pages = await import(pathToFileURL(join(temporary, 'pages.mjs')).href)
  const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
  const scenarios = { realistic: { fixture: realistic, fallback: null }, null: { fixture: {}, fallback: null },
    empty: { fixture: {}, fallback: [] }, withheld: { fixture: {}, fallback: { summary: null, consensus: null } },
    failed: { fixture: {}, fallback: null, error: '503 Synthetic failed request' },
    partial: { fixture: { ...realistic, useWorld: { market: {}, unknowns: null }, useCalibration: { measurable: true, buckets: null },
      useDailyReport: { date: '2026-10-16', executive_summary: null, summary: null, consensus: null, market: { top_bullish: [{ ticker: 'TEST', score: null }] } } }, fallback: null } }
  // React Router emits its standard SSR useLayoutEffect warning. Keep all
  // other warnings visible and fail on them (no broad console suppression).
  const originalError = console.error
  console.error = (...args) => {
    if (String(args[0]).startsWith('Warning: useLayoutEffect does nothing on the server')) return
    originalError(...args)
    throw new Error(`Unexpected React warning: ${args[0]}`)
  }
  try {
    for (const [scenario, { fixture, fallback, error = null }] of Object.entries(scenarios)) {
      globalThis.__launchFixture = fixture; globalThis.__launchDefault = fallback; globalThis.__launchError = error
      for (const [route, component] of Object.entries(routes)) {
        assert.ok(app.includes(`path="/${route}"`), `Missing route /${route}`)
        globalThis.__launchReads = []
        const html = renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: [`/${route}?symbol=AAPL&view=overview`] },
          React.createElement(pages.CurrencyProvider, null, React.createElement(pages[component]))))
        const text = html.replace(/<[^>]+>/g, ' ')
        assert.ok(text.trim().length > 20, `${route}/${scenario}: blank page`)
        if (scenario === 'realistic') assert.ok(text.includes(populated[route]), `${route}: populated fixture was not rendered`)
        const invalid = html.match(/.{0,80}(?:NaN|undefined|\[object Object\]|Infinity).{0,80}/)
        assert.ok(!invalid, `${route}/${scenario}: invalid display value: ${invalid?.[0]}`)
        if (['null', 'empty', 'withheld', 'failed'].includes(scenario)) assert.match(text, /unavailable|withheld|no exact|no signals|no strategies|not generated/i, `${route}/${scenario}: explicit missing-data state`)
        observed.push({ route: `/${route}`, scenario, reads: [...new Set(globalThis.__launchReads)] })
      }
    }
    const selected = { symbol: 'TEST', name: 'Synthetic listing', currency: 'USD', exchange: 'TEST' }
    for (const scenario of ['realistic', 'null', 'empty', 'withheld', 'failed']) {
      const data = scenario === 'realistic' ? { price: 100, currency: 'USD', fundamentals: { trailingPE: 15 },
        returns: { '1Y': 2 }, news: [{ title: 'Synthetic headline', source: 'Fixture', at: '2026-10-16' }], sentiment: { label: 'Neutral' } }
        : scenario === 'empty' ? [] : scenario === 'withheld' ? { summary: null, consensus: null, news: null, sentiment: { label: null } } : null
      const html = renderToStaticMarkup(React.createElement(MemoryRouter, null,
        React.createElement(pages.CurrencyProvider, null, React.createElement(pages.ExplorerDetails, {
          sel: selected, quote: data, dossier: data, news: data, embedded: true, newsState: scenario === 'failed' ? 'failed' : 'complete',
        }))))
      assert.ok(!/NaN|undefined|\[object Object\]|Infinity/.test(html), `Research details/${scenario}: invalid value`)
      assert.match(html, scenario === 'realistic' ? /Synthetic headline/ : /unavailable|withheld/)
    }
    const freshness = realistic.useDataFreshness
    const freshnessCases = [
      ...['COMPLETED', 'IN_PROGRESS', 'DID_NOT_FINISH', 'UNKNOWN'].map(state => ({
        data: { ...freshness, pipeline_run: { ...freshness.pipeline_run, state } }, expect: state.replaceAll('_', ' '),
      })),
      ...[null, [], { summary: null, consensus: null }, { datasets: [], pipeline_run: null }].map(data => ({ data, expect: 'unavailable' })),
      { data: null, error: '503 Synthetic failed freshness request', expect: '503 Synthetic failed freshness request' },
      { data: freshness, loading: true, expect: 'signals.json' }, // retained data during polling
      { data: null, loading: true, expect: 'Checking dataset freshness' },
    ]
    for (const { data, error = null, loading = false, expect } of freshnessCases) {
      globalThis.__launchFixture = { useDataFreshness: data }; globalThis.__launchDefault = null; globalThis.__launchError = error
      globalThis.__launchLoading = loading
      const html = renderToStaticMarkup(React.createElement(pages.DataFreshness))
      assert.ok(!/NaN|undefined|\[object Object\]|Infinity/.test(html), 'Freshness invalid display value')
      assert.ok(html.includes(expect), `Freshness expected ${expect}`)
      if (data?.datasets?.length && !error) {
        assert.equal((html.match(/<tbody><tr>|<\/tr><tr>/g) || []).length, 3, 'one row per dataset')
        assert.ok(html.includes('0m / 30.0h'), 'a zero age is valid')
        assert.ok(html.includes('unavailable / 2.0d'), 'unknown age is not zero')
        assert.ok(html.includes('file write time · not a market observation'))
        assert.ok(html.includes('LEGACY') && html.includes('2/3 rows dated'))
      }
    }
  } finally { console.error = originalError }
  console.log(`PASS: ${observed.length} offline route renders (8 routes × 6 payload states).`)
  console.log('PASS: 5 populated/empty/withheld/failed Research snapshot renders.')
  console.log('PASS: 11 freshness panel cases (four pipeline states, missing/empty/withheld/error/loading data; dataset ages, legacy and observation basis).')
  console.log('Scope: actual page JSX + synthetic useApi read hooks and loaded Research snapshot. SSR does not run effects, hydration, authentication, chart scripts or action handlers; nested effect-only loaders remain at their initial state. Run verify-technical-dashboard.mjs for populated technical evidence.')
} finally {
  globalThis.fetch = originalFetch
  for (const key of ['__launchFixture', '__launchDefault', '__launchError', '__launchReads', '__launchLoading']) delete globalThis[key]
  assert.ok(temporary.startsWith(join(root, 'node_modules', '.launch-render-')))
  rmSync(temporary, { recursive: true, force: true })
}
