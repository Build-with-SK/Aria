import React, { useEffect, useRef, useState, useCallback } from 'react'
import axios from 'axios'
import { useCurrency } from '../currency/CurrencyContext'
import LiveQuote from '../components/LiveQuote'
import ResearchAnalysis from '../components/ResearchAnalysis'

const mono = { fontFamily: 'var(--mono)' }
const CCY = { INR: '₹', GBP: '£', USD: '$', EUR: '€' }
// "GBp" is pence, London's actual quoting unit — 1576 GBp is £15.76, and
// rendering it as £1,576 is the classic 100× mistake.
const fmtPrice = (p, ccy) => {
  if (p == null) return '—'
  const n = Number(p)
  if (ccy === 'GBp') return `${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}p (£${(n / 100).toFixed(2)})`
  return (CCY[ccy] || '') + n.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

// map our universe symbol → a TradingView symbol
function tvSymbol(info) {
  if (!info) return ''
  const s = info.symbol, ex = info.exchange
  if (ex === 'NSE') return `NSE:${s}`
  if (ex === 'LSE') return `LSE:${s}`
  if (ex === 'CRYPTO') return (s || '').replace('-', '')      // BTC-USD → BTCUSD
  return s                                                     // US resolves on its own
}

// ─── TradingView advanced chart embed ─────────────────────────────────────────
/**
 * The embed script REWRITES the style of the element it is mounted on, replacing
 * our height with `height:100%`. A percentage height resolves against the parent,
 * so unless an ancestor has a definite height the whole chart collapses — it was
 * rendering ~148px tall instead of the intended full size.
 *
 * Fix: `autosize` lets the widget fill its box, and the box is given a real
 * height here (`CHART_HEIGHT`) rather than a percentage of something auto.
 */
const CHART_HEIGHT = 480

function TVChart({ tv, height = CHART_HEIGHT }) {
  const ref = useRef(null)
  useEffect(() => {
    if (!ref.current || !tv) return
    ref.current.innerHTML = '<div class="tradingview-widget-container__widget" style="height:100%;width:100%"></div>'
    const script = document.createElement('script')
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js'
    script.async = true
    script.innerHTML = JSON.stringify({
      symbol: tv, interval: 'D', timezone: 'Europe/London', theme: 'dark',
      style: '1', locale: 'en', hide_side_toolbar: true, allow_symbol_change: false,
      autosize: true, backgroundColor: 'rgba(7,7,7,1)',
    })
    ref.current.appendChild(script)
  }, [tv])
  // height:100% of a parent that has a definite height — see the note above.
  return (
    <div style={{ height, width: '100%' }}>
      <div ref={ref} className="tradingview-widget-container" style={{ height: '100%', width: '100%' }} />
    </div>
  )
}

// ─── sentiment badge ──────────────────────────────────────────────────────────
const sentColor = (l) => l === 'Bullish' ? 'var(--green)' : l === 'Bearish' ? 'var(--red)' : 'var(--yellow)'

// ─── page ─────────────────────────────────────────────────────────────────────
/**
 * `symbol` prop: when embedded in Research, the parent owns the search box and
 * hands this view the chosen symbol.
 */
export default function Explorer({ symbol: externalSymbol = null, embedded = false }) {
  const { price: toLocal } = useCurrency()
  const [q, setQ] = useState('')
  const [results, setResults] = useState([])
  const [sel, setSel] = useState(null)          // selected search-result info
  const [quote, setQuote] = useState(null)
  const [dossier, setDossier] = useState(null)
  const [news, setNews] = useState(null)
  const [loading, setLoading] = useState(false)
  const tRef = useRef(null)

  // debounced search
  useEffect(() => {
    clearTimeout(tRef.current)
    if (!q.trim()) { setResults([]); return }
    tRef.current = setTimeout(() => {
      axios.get('/api/universe/search', { params: { q: q.trim(), n: 12 } })
        .then(r => setResults(Array.isArray(r.data) ? r.data : r.data.results || []))
        .catch(() => setResults([]))
    }, 250)
    return () => clearTimeout(tRef.current)
  }, [q])

  const select = useCallback((info) => {
    setSel(info); setResults([]); setQ(info.symbol)
    setQuote(null); setDossier(null); setNews(null); setLoading(true)
    axios.get(`/api/universe/quote/${info.symbol}`).then(r => setQuote(r.data)).catch(() => {})
    axios.get(`/api/universe/news/${info.symbol}`).then(r => setNews(r.data)).catch(() => {})
    axios.get(`/api/universe/dossier/${info.symbol}`).then(r => setDossier(r.data)).catch(() => {}).finally(() => setLoading(false))
  }, [])

  // Driven from outside: resolve the parent's symbol through the universe so
  // this view gets the same {symbol, exchange, currency, name} shape the search
  // dropdown would have handed it.
  useEffect(() => {
    if (!externalSymbol || externalSymbol === sel?.symbol) return
    axios.get('/api/universe/search', { params: { q: externalSymbol, n: 5 } })
      .then(r => {
        const rows = Array.isArray(r.data) ? r.data : r.data.results || []
        const exact = rows.find(x => (x.symbol || '').toUpperCase() === externalSymbol.toUpperCase())
        if (exact || rows[0]) select(exact || rows[0])
      })
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalSymbol])

  const ccy = quote?.currency || sel?.currency || 'USD'
  // The quote endpoint reports the native currency; hand it to the converter
  // explicitly rather than letting it guess from the symbol.
  const localPrice = toLocal(quote?.price, { from: quote?.currency || sel?.currency })
  const change = quote?.change_pct
  const tv = tvSymbol(sel)

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto' }}>
      {/* Header + search — suppressed when Research owns the search */}
      {!embedded && <>
      <div style={{ marginBottom: 10 }}>
        <div style={{ ...mono, fontSize: 16, fontWeight: 800, color: 'var(--orange)' }}>EXPLORER</div>
        <div style={{ ...mono, fontSize: 10, color: 'var(--text-dim)' }}>
          Search any symbol · India (₹ NSE) · UK (£ LSE) · US ($) · crypto — fresh data, news & sentiment on demand
        </div>
      </div>

      <div style={{ position: 'relative', marginBottom: 14 }}>
        <input
          value={q} onChange={e => setQ(e.target.value)}
          placeholder="Search ticker or company — e.g. RELIANCE, HSBA, AAPL, BP, TCS…"
          style={{ ...mono, width: '100%', background: '#0d0d0d', border: '1px solid #222', color: '#ddd', fontSize: 13, padding: '10px 14px', borderRadius: 6, outline: 'none', boxSizing: 'border-box' }}
        />
        {results.length > 0 && (
          <div style={{ position: 'absolute', top: 44, left: 0, right: 0, zIndex: 10, background: '#0a0a0a', border: '1px solid #222', borderRadius: 6, maxHeight: 320, overflowY: 'auto' }}>
            {results.map((r, i) => (
              <div key={i} onClick={() => select(r)} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '8px 12px', cursor: 'pointer', borderBottom: '1px solid #141414' }}
                onMouseEnter={e => e.currentTarget.style.background = '#141414'} onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                <span style={{ ...mono, fontSize: 12, fontWeight: 700, color: '#fff', minWidth: 90 }}>{r.symbol}</span>
                <span style={{ ...mono, fontSize: 9, color: 'var(--orange)', minWidth: 44 }}>{r.exchange}</span>
                <span style={{ ...mono, fontSize: 9, color: CCY[r.currency] ? 'var(--green)' : 'var(--muted)', minWidth: 30 }}>{CCY[r.currency] || ''}</span>
                <span style={{ ...mono, fontSize: 11, color: 'var(--text-dim)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      </>}

      {!sel && !embedded && (
        <div style={{ ...mono, fontSize: 12, color: '#444', textAlign: 'center', padding: 50, border: '1px dashed #1a1a1a', borderRadius: 8 }}>
          Search a symbol above. The universe holds 3,000+ symbols (India, UK, US, crypto) — nothing is loaded until you look one up, then everything is fetched fresh.
        </div>
      )}

      {sel && (
        <div>
          {/* header — live price, market state, day range */}
          <div style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
              <span style={{ ...mono, fontSize: 22, fontWeight: 800, color: '#fff' }}>{sel.symbol}</span>
              <span style={{ ...mono, fontSize: 11, color: 'var(--orange)' }}>{sel.exchange} · {ccy}</span>
              <span style={{ ...mono, fontSize: 11, color: 'var(--text-dim)', flex: 1 }}>{sel.name}</span>
              {loading && <span style={{ ...mono, fontSize: 10, color: 'var(--yellow)' }}>loading…</span>}
            </div>
            {/* Polls the live endpoint. The old header read a 15-minute-cached
                DAILY CLOSE, which is why it never moved. */}
            <LiveQuote symbol={sel.symbol} nativeCurrency={ccy} />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: 14 }}>
            {/* left: chart + fundamentals */}
            <div>
              {/* Definite height: the embed sizes itself against this box. */}
              <div style={{
                border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden',
                marginBottom: 14, background: '#070707', height: CHART_HEIGHT,
              }}>
                {tv ? <TVChart tv={tv} /> : <div style={{ ...mono, padding: 30, color: '#444' }}>no chart</div>}
              </div>
              {dossier && !dossier.error && (
                <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14, background: '#070707' }}>
                  <div style={{ ...mono, fontSize: 11, fontWeight: 800, color: 'var(--orange)', letterSpacing: 2, marginBottom: 10 }}>FUNDAMENTALS</div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '8px 16px' }}>
                    {/* These live under dossier.fundamentals, not at the top
                        level — reading them from the root made every single
                        field render as a dash. */}
                    {(() => {
                      const f = dossier.fundamentals || {}
                      const r = dossier.returns || {}
                      const n1 = v => (typeof v === 'number' ? v.toFixed(1) : null)
                      const n2 = v => (typeof v === 'number' ? v.toFixed(2) : null)
                      const p1 = v => (typeof v === 'number' ? (v * 100).toFixed(1) + '%' : null)
                      const ret = v => (typeof v === 'number' ? (v > 0 ? '+' : '') + v + '%' : null)
                      // dividendYield's units vary by yfinance version; derive
                      // from the payout rate where possible (see currency.py).
                      const dy = (typeof f.dividendRate === 'number' && dossier.price)
                        ? (f.dividendRate / dossier.price * 100).toFixed(2) + '%'
                        : (typeof f.dividendYield === 'number' && f.dividendYield >= 1
                            ? f.dividendYield.toFixed(2) + '%' : null)
                      return [
                        ['Sector', f.sector], ['Industry', f.industry],
                        ['Market Cap', typeof f.marketCap === 'number'
                          ? (f.marketCap / 1e9).toFixed(1) + 'B ' + (f.currency || ccy) : null],
                        ['P/E', n1(f.trailingPE)], ['P/B', n2(f.priceToBook)],
                        ['ROE', p1(f.returnOnEquity)],
                        ['Div Yield', dy],
                        ['1Y Return', ret(r['1Y'])],
                      ]
                    })().map(([k, v], i) => (
                      <div key={i}>
                        <div style={{ ...mono, fontSize: 9, color: 'var(--muted)' }}>{k}</div>
                        <div style={{ ...mono, fontSize: 12, color: '#ddd' }}>{v ?? '—'}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* right: news + sentiment */}
            <div>
              <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14, background: '#070707' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                  <span style={{ ...mono, fontSize: 11, fontWeight: 800, color: 'var(--orange)', letterSpacing: 2 }}>NEWS & SENTIMENT</span>
                  {news?.sentiment && (
                    <span style={{ ...mono, fontSize: 9, fontWeight: 800, color: '#000', background: sentColor(news.sentiment.label), borderRadius: 3, padding: '2px 6px' }}>
                      {news.sentiment.label.toUpperCase()}
                    </span>
                  )}
                </div>
                {news?.political_exposure && news.political_exposure !== 'Low' && (
                  <div style={{ ...mono, fontSize: 9, color: 'var(--yellow)', marginBottom: 8 }}>⚑ Political exposure: {news.political_exposure}</div>
                )}
                {!news ? <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>fetching fresh news…</div>
                  : news.news?.length === 0 ? <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>{news.source}</div>
                  : news.news.map((a, i) => (
                    <a key={i} href={a.url} target="_blank" rel="noreferrer" style={{ display: 'block', textDecoration: 'none', padding: '7px 0', borderBottom: '1px solid #141414' }}>
                      <div style={{ ...mono, fontSize: 11, color: '#cdd', lineHeight: 1.4 }}>{a.title}</div>
                      <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 2 }}>{a.source} · {a.at?.slice(0, 10)}</div>
                    </a>
                  ))}
              </div>
            </div>
          </div>
          {/* Technical + fundamental, computed on live data */}
          <div style={{ marginTop: 14 }}>
            <ResearchAnalysis symbol={sel.symbol} dossier={dossier} currency={ccy} />
          </div>
        </div>
      )}
    </div>
  )
}
