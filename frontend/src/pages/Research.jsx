/**
 * pages/Research.jsx — EXPLORER + NEXUS, merged.
 *
 * They were the same page asked at two depths: Explorer looked a symbol up
 * (quote, chart, fundamentals, news); Nexus scored it deterministically from
 * configs/nexus_rules.yaml with evidence-linked SWOT and peers. Two search
 * boxes for one question is a navigation problem, not a feature.
 *
 * One search now owns the symbol; the two views are tabs beneath it.
 *
 * This page is also the landing target for every ticker in the app — the tape,
 * the signal tables, the ML predictions all deep-link to
 * `/research?symbol=XXX`, which is why the symbol lives in the URL rather than
 * in component state alone.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import axios from 'axios'
import { TabBar } from '../components/UI'
import Explorer from './Explorer'
import Nexus from './Nexus'

const MONO = 'var(--mono)'
const CCY_SYMBOL = { INR: '₹', GBP: '£', USD: '$', EUR: '€', JPY: '¥' }

export default function Research() {
  const [params, setParams] = useSearchParams()
  const urlSymbol = (params.get('symbol') || '').toUpperCase()
  const urlTab = params.get('view') === 'deep' ? 'deep' : 'overview'

  const [q, setQ] = useState('')
  const [results, setResults] = useState([])
  const [symbol, setSymbol] = useState(urlSymbol)
  const [tab, setTab] = useState(urlTab)
  const debounce = useRef(null)

  // The URL is the source of truth, so a deep link and a back button both work.
  useEffect(() => { setSymbol(urlSymbol) }, [urlSymbol])
  useEffect(() => { setTab(urlTab) }, [urlTab])

  useEffect(() => {
    clearTimeout(debounce.current)
    if (!q.trim() || q.trim().toUpperCase() === symbol) { setResults([]); return }
    debounce.current = setTimeout(() => {
      axios.get('/api/universe/search', { params: { q: q.trim(), n: 12 } })
        .then(r => setResults(Array.isArray(r.data) ? r.data : r.data.results || []))
        .catch(() => setResults([]))
    }, 240)
    return () => clearTimeout(debounce.current)
  }, [q, symbol])

  const choose = useCallback((sym, view) => {
    setResults([])
    setQ('')
    setParams({ symbol: String(sym).toUpperCase(), view: view || tab }, { replace: false })
  }, [setParams, tab])

  const switchTab = (id) => {
    setTab(id)
    if (symbol) setParams({ symbol, view: id }, { replace: true })
  }

  return (
    <div>
      <div style={{ marginBottom: 10 }}>
        <div style={{ fontFamily: MONO, fontSize: 16, fontWeight: 800, color: 'var(--orange)', letterSpacing: '.12em' }}>
          ◬ RESEARCH
        </div>
        <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--muted)' }}>
          Any listing — India (₹ NSE/BSE) · UK (£ LSE) · US ($) · crypto · FX. Nothing is
          preloaded; everything is fetched fresh when you look it up.
        </div>
      </div>

      {/* the one search */}
      <div style={{ position: 'relative', maxWidth: 620, marginBottom: 14 }}>
        <input
          value={q}
          onChange={e => setQ(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && q.trim()) choose(q.trim()) }}
          placeholder={symbol ? `${symbol} — search another symbol…`
            : 'Search ticker or company — RELIANCE, HSBA, AAPL, BP, TCS, BTC…'}
          spellCheck={false}
          style={{
            width: '100%', boxSizing: 'border-box', background: '#0d0d0d',
            border: '1px solid var(--border-2)', color: 'var(--text)', fontFamily: MONO,
            fontSize: 13, padding: '10px 14px', outline: 'none', borderRadius: 4,
          }} />
        {results.length > 0 && (
          <div style={{
            position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
            background: '#0a0a0a', border: '1px solid var(--border-2)', borderTop: 'none',
            maxHeight: 340, overflowY: 'auto', borderRadius: '0 0 4px 4px',
          }}>
            {results.map((r, i) => (
              <div key={`${r.symbol}-${i}`} onClick={() => choose(r.symbol)}
                style={{
                  display: 'flex', gap: 10, alignItems: 'baseline', padding: '8px 12px',
                  cursor: 'pointer', borderBottom: '1px solid #141414',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = '#141414')}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 700, color: 'var(--orange)', minWidth: 96 }}>{r.symbol}</span>
                <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--muted)', minWidth: 42 }}>{r.exchange}</span>
                <span style={{ fontFamily: MONO, fontSize: 10, color: 'var(--green)', minWidth: 20 }}>{CCY_SYMBOL[r.currency] || ''}</span>
                <span style={{ fontSize: 11, color: 'var(--text)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
                {r.has_fno ? (
                  <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--yellow)', border: '1px solid rgba(255,204,0,.3)', borderRadius: 2, padding: '0 4px' }}>
                    F&O·{r.lot_size}
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>

      {!symbol && (
        <div style={{
          fontFamily: MONO, fontSize: 12, color: '#555', textAlign: 'center', padding: 54,
          border: '1px dashed #1a1a1a', borderRadius: 8,
        }}>
          Search a symbol above — or click any ticker anywhere in ARIA to land here.
        </div>
      )}

      {symbol && (
        <>
          <TabBar
            tabs={[{ id: 'overview', label: 'Overview', icon: '⌕' },
                   { id: 'deep', label: 'Deep research', icon: '◬' }]}
            active={tab} onChange={switchTab} />
          {/* Both views stay mounted so switching tabs does not refetch the
              dossier; only the active one is shown. */}
          <div style={{ display: tab === 'overview' ? 'block' : 'none' }}>
            <Explorer symbol={symbol} embedded />
          </div>
          <div style={{ display: tab === 'deep' ? 'block' : 'none' }}>
            <Nexus symbol={symbol} embedded />
          </div>
        </>
      )}
    </div>
  )
}
