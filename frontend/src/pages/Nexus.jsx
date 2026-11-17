import React, { useEffect, useRef, useState } from 'react'
import { searchUniverse, fetchNexusResearch } from '../hooks/useApi'
import { tradingViewTheme } from '../components/Accessibility'

/* ─────────────────────────────────────────────────────────────────────────
   NEXUS — deterministic, evidence-linked deep research terminal.
   Every number on this page carries its source; PASS/FAIL verdicts and the
   /10 score come from configs/nexus_rules.yaml, not from an LLM.
   ───────────────────────────────────────────────────────────────────────── */

const SERIES_COLORS = ['var(--ink)', 'var(--accent)', 'var(--blue)', 'var(--yellow)', 'var(--red)']

function SrcLine({ children }) {
  return (
    <div style={{ fontFamily: 'var(--font)', fontSize: 12, color: 'var(--ink-3)', marginTop: 8, }}>
      Src ▸ {children}
    </div>
  )
}

function SourceChip({ label }) {
  return (
    <span style={{
      fontFamily: 'var(--font)', fontSize: 12, color: 'var(--green)',
      background: 'var(--green-bg)', border: '1px solid color-mix(in srgb, var(--green) 25%, transparent)',
      borderRadius: 8, padding: '0px 5px', marginLeft: 6, whiteSpace: 'nowrap',
    }}>{label}</span>
  )
}

function StatusChip({ status }) {
  const map = {
    PASS: { c: 'var(--green)', bg: 'var(--green-bg)', b: 'color-mix(in srgb, var(--green) 30%, transparent)' },
    FAIL: { c: 'var(--red)', bg: 'var(--red-bg)', b: 'color-mix(in srgb, var(--red) 30%, transparent)' },
    NA:   { c: 'var(--ink-3)', bg: 'transparent', b: 'var(--line-2)' },
  }
  const s = map[status] || map.NA
  return (
    <span style={{
      fontFamily: 'var(--font)', fontSize: 12, fontWeight: 600, color: s.c,
      background: s.bg, border: `1px solid ${s.b}`, borderRadius: 8,
      padding: '1px 8px', minWidth: 44, textAlign: 'center', display: 'inline-block',
    }}>{status}</span>
  )
}

/* Normalized multi-series SVG line chart (base-100 at window start). */
function CompareChart({ series, window: win }) {
  const W = 640, H = 220, PAD = 8
  const drawn = series.filter(s => s.points && Object.keys(s.points).length > 2)
  if (!drawn.length) return <div className="muted mono" style={{ fontSize: 13, padding: 20 }}>No series cached yet</div>

  const sliced = drawn.map(s => {
    const entries = Object.entries(s.points)
    const cut = win === '1Y' ? entries.slice(-52) : entries
    const base = cut[0][1]
    return { ...s, vals: cut.map(([, v]) => (v / base) * 100) }
  })
  const all = sliced.flatMap(s => s.vals)
  const min = Math.min(...all), max = Math.max(...all)
  const y = v => H - PAD - ((v - min) / (max - min || 1)) * (H - 2 * PAD)

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
        {[0.25, 0.5, 0.75].map(f => (
          <line key={f} x1={0} x2={W} y1={H * f} y2={H * f} stroke="var(--line)" strokeWidth="1" />
        ))}
        <line x1={0} x2={W} y1={y(100)} y2={y(100)} stroke="var(--line-2)" strokeDasharray="4 4" />
        {sliced.map((s, i) => (
          <polyline
            key={s.label} fill="none" stroke={s.color} strokeWidth={i === 0 ? 1.8 : 1.2}
            opacity={i === 0 ? 1 : 0.8}
            points={s.vals.map((v, j) => `${(j / (s.vals.length - 1)) * W},${y(v)}`).join(' ')}
          />
        ))}
      </svg>
      <div style={{ display: 'flex', gap: 14, marginTop: 6, flexWrap: 'wrap' }}>
        {sliced.map(s => (
          <span key={s.label} style={{ fontFamily: 'var(--font)', fontSize: 12, color: s.color }}>
            ── {s.label} <span className="muted">{s.vals[s.vals.length - 1].toFixed(0)}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

/* Official TradingView advanced-chart embed (no npm package, script injection
   only). Gives NEXUS live global coverage — any exchange, currency, futures —
   without ARIA storing a byte of that data. */
function tvSymbol(report) {
  const s = report.symbol
  // NSE realtime data is licence-locked in free embeds; most NSE large caps
  // are dual-listed on BSE, which TradingView serves freely with the same ticker
  if (report.exchange === 'NSE') return `BSE:${s}`
  if (report.asset_class === 'crypto') return s.replace('-USD', 'USD')
  if (report.asset_class === 'forex') return 'FX_IDC:' + s.replace('=X', '')
  if (report.asset_class === 'index') return s.replace('^', '')
  return s
}

function TVChart({ symbol }) {
  const ref = useRef(null)
  useEffect(() => {
    if (!ref.current) return
    ref.current.innerHTML = ''
    const container = document.createElement('div')
    container.className = 'tradingview-widget-container'
    const inner = document.createElement('div')
    inner.className = 'tradingview-widget-container__widget'
    container.appendChild(inner)
    const script = document.createElement('script')
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js'
    script.async = true
    script.innerHTML = JSON.stringify({
      symbol, interval: 'D', style: '1', locale: 'en',
      width: '100%', height: 430, allow_symbol_change: true,
      hide_side_toolbar: false, ...tradingViewTheme(),
    })
    container.appendChild(script)
    ref.current.appendChild(container)
    return () => { if (ref.current) ref.current.innerHTML = '' }
  }, [symbol])
  return <div ref={ref} style={{ minHeight: 430 }} />
}

function num(v, digits = 2) {
  if (v === null || v === undefined) return 'N/A'
  if (Math.abs(v) >= 1e12) return (v / 1e12).toFixed(2) + 'T'
  if (Math.abs(v) >= 1e9) return (v / 1e9).toFixed(2) + 'B'
  if (Math.abs(v) >= 1e7) return (v / 1e7).toFixed(2) + 'Cr'
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: digits })
}

/**
 * `symbol` prop: when the page is embedded in Research, the surrounding page
 * owns the search box and drives the symbol. The internal search is then
 * hidden — two search bars for one symbol is exactly the confusion the merge
 * was meant to remove.
 */
export default function Nexus({ symbol: externalSymbol = null, embedded = false }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [win, setWin] = useState('5Y')
  const debounce = useRef(null)
  const chosen = useRef(null)

  useEffect(() => {
    if (!query || query.length < 2 || query === chosen.current) { setResults([]); return }
    clearTimeout(debounce.current)
    debounce.current = setTimeout(async () => {
      try { setResults(await searchUniverse(query)) } catch { setResults([]) }
    }, 220)
    return () => clearTimeout(debounce.current)
  }, [query])

  const select = async (symbol) => {
    chosen.current = symbol
    setResults([]); setQuery(symbol); setLoading(true); setError(null)
    try {
      setReport(await fetchNexusResearch(symbol))
    } catch (e) {
      setError(e.message); setReport(null)
    } finally {
      setLoading(false)
    }
  }

  // Driven from outside: run the report whenever the parent's symbol changes.
  useEffect(() => {
    if (externalSymbol && externalSymbol !== chosen.current) select(externalSymbol)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalSymbol])

  const f = report?.fundamentals || {}
  const ret = report?.returns || {}
  const retColor = v => v === null || v === undefined ? 'var(--ink-3)' : v >= 0 ? 'var(--green)' : 'var(--red)'
  const scoreColor = report ? (report.score.total >= 7 ? 'var(--green)' : report.score.total >= 4 ? 'var(--yellow)' : 'var(--red)') : 'var(--ink-3)'

  const chartSeries = report ? [
    { label: report.symbol, points: report.weekly_closes_5y, color: SERIES_COLORS[0] },
    ...(report.peers || []).filter(p => p.series).slice(0, 4).map((p, i) => ({
      label: p.symbol, points: p.series, color: SERIES_COLORS[(i + 1) % SERIES_COLORS.length],
    })),
  ] : []

  return (
    <div>
      {/* Header + search — suppressed when the Research page owns the search */}
      {!embedded && <>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 4 }}>
        <div style={{ fontFamily: 'var(--font)', fontSize: 17, fontWeight: 600, color: 'var(--accent)', }}>
          ◬ NEXUS
        </div>
        <div style={{ fontFamily: 'var(--font)', fontSize: 12, color: 'var(--ink-3)', }}>
          // Deep research — deterministic · evidence-linked
        </div>
      </div>
      <div style={{ position: 'relative', maxWidth: 520, marginBottom: 18 }}>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search any symbol — NSE · LSE · US · crypto · FX…"
          spellCheck={false}
          style={{
            width: '100%', background: 'var(--inset)', border: '1px solid var(--line-2)',
            color: 'var(--ink)', fontFamily: 'var(--font)', fontSize: 14,
            padding: '9px 12px', outline: 'none', borderRadius: 8, 
          }}
        />
        {results.length > 0 && (
          <div style={{
            position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
            background: 'var(--surface)', border: '1px solid var(--line-2)', borderTop: 'none',
          }}>
            {results.map(r => (
              <div key={r.symbol} onClick={() => select(r.symbol)} style={{
                display: 'flex', gap: 10, padding: '7px 12px', cursor: 'pointer',
                borderBottom: '1px solid var(--line)', alignItems: 'baseline',
              }}
                onMouseEnter={e => e.currentTarget.style.background = 'var(--accent-bg)'}
                onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                <span style={{ fontFamily: 'var(--font)', fontSize: 14, fontWeight: 600, color: 'var(--accent)', minWidth: 90 }}>{r.symbol}</span>
                <span style={{ fontSize: 13, color: 'var(--ink)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
                {r.has_fno ? (
                  <span style={{ fontFamily: 'var(--font)', fontSize: 12, color: 'var(--yellow)', border: '1px solid color-mix(in srgb, var(--yellow) 30%, transparent)', borderRadius: 8, padding: '0 4px' }}>
                    F&O·{r.lot_size}
                  </span>
                ) : null}
                <span style={{ fontFamily: 'var(--font)', fontSize: 12, color: 'var(--ink-3)' }}>{r.exchange}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      </>}

      {loading && (
        <div style={{ fontFamily: 'var(--font)', fontSize: 14, color: 'var(--accent)', padding: 30 }}>
          ▸ Streaming dossier<span className="blink">█</span>
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>Five years of prices and fundamentals — usually a few seconds</div>
        </div>
      )}
      {error && <div className="mono" style={{ color: 'var(--red)', fontSize: 14, padding: 20 }}>Error ▸ {error}</div>}

      {report && !loading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

          {/* Row 1 — snapshot + score */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 380px', gap: 14 }}>
            <div className="bb-card">
              <div className="bb-card-header">{report.symbol} — {report.name} <span style={{ color: 'var(--ink-3)' }}>· {report.exchange}</span></div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 16, marginBottom: 12 }}>
                <span style={{ fontFamily: 'var(--font)', fontSize: 34, fontWeight: 600, color: 'var(--ink)' }}>
                  {num(report.price)} <span style={{ fontSize: 14, color: 'var(--ink-3)' }}>{report.currency}</span>
                </span>
                {['1M', '6M', '1Y', '5Y'].map(k => (
                  <span key={k} style={{ fontFamily: 'var(--font)', fontSize: 14 }}>
                    <span className="muted" style={{ fontSize: 12 }}>{k} </span>
                    <span style={{ color: retColor(ret[k]), fontWeight: 600 }}>
                      {ret[k] === null || ret[k] === undefined ? 'N/A' : `${ret[k] > 0 ? '+' : ''}${ret[k]}%`}
                    </span>
                  </span>
                ))}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '8px 16px' }}>
                {[
                  ['MKT CAP', num(f.marketCap)], ['P/E', num(f.trailingPE)], ['FWD P/E', num(f.forwardPE)], ['P/B', num(f.priceToBook)],
                  ['ROE', f.returnOnEquity != null ? (f.returnOnEquity * 100).toFixed(1) + '%' : 'N/A'],
                  ['NET MARGIN', f.profitMargins != null ? (f.profitMargins * 100).toFixed(1) + '%' : 'N/A'],
                  ['D/E', f.debtToEquity != null ? num(f.debtToEquity) + '%' : 'N/A'],
                  ['DIV YIELD', f.dividendYield != null ? num(f.dividendYield) + '%' : 'N/A'],
                  ['52W HIGH', num(f.fiftyTwoWeekHigh)], ['52W LOW', num(f.fiftyTwoWeekLow)],
                  ['BETA', num(f.beta)], ['INSIDERS', f.heldPercentInsiders != null ? (f.heldPercentInsiders * 100).toFixed(1) + '%' : 'N/A'],
                ].map(([k, v]) => (
                  <div key={k}>
                    <div className="muted mono" style={{ fontSize: 12, }}>{k}</div>
                    <div className="mono white" style={{ fontSize: 14, fontWeight: 600 }}>{v}</div>
                  </div>
                ))}
              </div>
              <SrcLine>{report.sources.prices} · fundamentals: {report.sources.fundamentals}</SrcLine>
            </div>

            <div className="bb-card">
              <div className="bb-card-header">Score — how it got there</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 10 }}>
                <span style={{ fontFamily: 'var(--font)', fontSize: 42, fontWeight: 600, color: scoreColor }}>
                  {report.score.total}<span style={{ fontSize: 16, color: 'var(--ink-3)' }}>/{report.score.out_of}</span>
                </span>
              </div>
              {report.score.components.map(c => (
                <div key={c.name} style={{ marginBottom: 8 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font)', fontSize: 12 }}>
                    <span style={{ color: 'var(--ink)' }}>{c.name.toUpperCase()}</span>
                    <span style={{ color: c.points === 2 ? 'var(--green)' : c.points === 1 ? 'var(--yellow)' : 'var(--red)', fontWeight: 600 }}>{c.points}/{c.max}</span>
                  </div>
                  <div className="bar-track" style={{ margin: '3px 0' }}>
                    <div className={c.points === 2 ? 'bar-fill-bull' : c.points === 1 ? 'bar-fill-neut' : 'bar-fill-bear'} style={{ width: `${(c.points / c.max) * 100}%` }} />
                  </div>
                  <div className="muted" style={{ fontSize: 12, fontFamily: 'var(--font)', lineHeight: 1.5 }}>{c.evidence}</div>
                </div>
              ))}
              <SrcLine>{report.score.method}</SrcLine>
            </div>
          </div>

          {/* Row 2 — chart + red flags */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 380px', gap: 14 }}>
            <div className="bb-card">
              <div className="bb-card-header" style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span>Price — {report.symbol} vs peers (base 100)</span>
                <span>
                  {['1Y', '5Y'].map(k => (
                    <span key={k} onClick={() => setWin(k)} style={{
                      cursor: 'pointer', marginLeft: 10, fontSize: 12,
                      color: win === k ? 'var(--accent)' : 'var(--ink-3)',
                      borderBottom: win === k ? '1px solid var(--accent)' : 'none',
                    }}>{k}</span>
                  ))}
                </span>
              </div>
              <CompareChart series={chartSeries} window={win} />
              <SrcLine>Yahoo Finance weekly closes · rebased to 100 at the start of the window · peers added as they load</SrcLine>
            </div>

            <div className="bb-card">
              <div className="bb-card-header">
                Red flags <span style={{ color: report.red_flags.failed ? 'var(--red)' : 'var(--green)' }}>
                  — {report.red_flags.failed} fail / {report.red_flags.items.length}
                </span>
              </div>
              {report.red_flags.items.map(x => (
                <div key={x.id} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '6px 0', borderBottom: '1px solid var(--line)' }}>
                  <StatusChip status={x.status} />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 13, color: 'var(--ink)' }}>{x.label}</div>
                    <div className="muted mono" style={{ fontSize: 12 }}>{x.evidence}</div>
                  </div>
                </div>
              ))}
              <SrcLine>{report.red_flags.source_note}</SrcLine>
            </div>
          </div>

          {/* Row 2.5 — TradingView global chart */}
          <div className="bb-card">
            <div className="bb-card-header">Global chart — TradingView · {tvSymbol(report)}</div>
            <TVChart symbol={tvSymbol(report)} />
            <SrcLine>Chart and data by TradingView · not stored by ARIA</SrcLine>
          </div>

          {/* Row 3 — SWOT */}
          <div className="bb-card">
            <div className="bb-card-header">SWOT — evidence-linked</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px 24px' }}>
              {[
                ['STRENGTHS', 'strengths', 'var(--green)'],
                ['WEAKNESSES', 'weaknesses', 'var(--red)'],
                ['OPPORTUNITIES', 'opportunities', 'var(--blue)'],
                ['THREATS', 'threats', 'var(--yellow)'],
              ].map(([title, key, color]) => (
                <div key={key}>
                  <div style={{ fontFamily: 'var(--font)', fontSize: 12, fontWeight: 600, color, marginBottom: 6 }}>{title}</div>
                  {(report.swot[key] || []).length === 0 && <div className="muted mono" style={{ fontSize: 12 }}>— none detected from available data</div>}
                  {(report.swot[key] || []).map((b, i) => (
                    <div key={i} style={{ fontSize: 13, color: 'var(--ink)', padding: '3px 0', lineHeight: 1.5 }}>
                      <span style={{ color, marginRight: 6 }}>▪</span>
                      {b.text}
                      <SourceChip label={b.source} />
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <SrcLine>every bullet computed from the numbers shown above — no unsourced claims</SrcLine>
          </div>

          {/* Row 4 — peers */}
          <div className="bb-card">
            <div className="bb-card-header">Peers — same sector · top by mcap</div>
            {(report.peers || []).length === 0
              ? <div className="muted mono" style={{ fontSize: 13 }}>
                  NO PEERS MAPPED YET — sector peers appear as more symbols in this sector are researched
                </div>
              : <table>
                  <thead><tr><th>Symbol</th><th>Name</th><th>Mkt cap</th><th>P/E</th><th>ROE</th><th>1Y</th><th>Chart</th></tr></thead>
                  <tbody>
                    {report.peers.map(p => (
                      <tr key={p.symbol} style={{ cursor: 'pointer' }} onClick={() => select(p.symbol)}>
                        <td className="acc" style={{ fontWeight: 600 }}>{p.symbol}</td>
                        <td>{p.name}</td>
                        <td>{num(p.mcap)}</td>
                        <td>{num(p.trailingPE)}</td>
                        <td>{p.returnOnEquity != null ? (p.returnOnEquity * 100).toFixed(1) + '%' : 'N/A'}</td>
                        <td style={{ color: retColor(p.returns?.['1Y']) }}>{p.returns?.['1Y'] != null ? `${p.returns['1Y'] > 0 ? '+' : ''}${p.returns['1Y']}%` : 'N/A'}</td>
                        <td className="muted">{p.series ? '● ready' : '○ loading…'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>}
            <SrcLine>Select a peer to open its research</SrcLine>
          </div>

          <div className="muted mono" style={{ fontSize: 12, textAlign: 'center', padding: '4px 0 12px' }}>
            {report.disclaimer} · generated {report.generated_at?.slice(0, 19).replace('T', ' ')}
          </div>
        </div>
      )}

      {!report && !loading && !error && (
        <div style={{ fontFamily: 'var(--font)', fontSize: 13, color: 'var(--ink-3)', padding: '40px 0', lineHeight: 2 }}>
          ▸ type a symbol or company name — RELIANCE, TCS, AAPL, BTC-USD…<br />
          ▸ tier-0 index answers instantly · tier-2 dossier streams in only for the symbol you open<br />
          ▸ verdicts are deterministic (configs/nexus_rules.yaml) · every bullet carries its source
        </div>
      )}
    </div>
  )
}
