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
import { SectionHeader } from '../components/UI'
import Explorer from './Explorer'
import Nexus from './Nexus'
import V5 from './V5'
import ResearchAnalysis from '../components/ResearchAnalysis'
import { isExactGoldContract } from '../components/technicalEvidence'

const MONO = 'var(--font)'
const CCY_SYMBOL = { INR: '₹', GBP: '£', USD: '$', EUR: '€', JPY: '¥' }

export default function Research() {
  const [params, setParams] = useSearchParams()
  const urlSymbol = (params.get('symbol') || '').toUpperCase()
  const urlTab = params.get('view') === 'deep' ? 'deep' : 'overview'

  const [q, setQ] = useState('')
  const [results, setResults] = useState([])
  const [symbol, setSymbol] = useState(urlSymbol)
  const [tab, setTab] = useState(urlTab)
  const [indexed, setIndexed] = useState(null)
  const deep = tab === 'deep'
  const debounce = useRef(null)

  // The subtitle quotes the index size the server reports, never a number
  // typed into this file — three of those were already stale (766, 3,000+,
  // 3,143) against an index of 22,022.
  useEffect(() => {
    axios.get('/api/universe/status')
      .then(r => setIndexed(r.data?.total_symbols || null))
      .catch(() => setIndexed(null))
  }, [])

  // The URL is the source of truth, so a deep link and a back button both work.
  useEffect(() => { setSymbol(urlSymbol) }, [urlSymbol])
  useEffect(() => { setTab(urlTab) }, [urlTab])

  useEffect(() => {
    clearTimeout(debounce.current)
    let cancelled = false
    if (!q.trim() || q.trim().toUpperCase() === symbol) { setResults([]); return }
    debounce.current = setTimeout(() => {
      axios.get('/api/universe/search', { params: { q: q.trim(), n: 12 } })
        .then(r => { if (!cancelled) setResults(Array.isArray(r.data) ? r.data : r.data.results || []) })
        .catch(() => { if (!cancelled) setResults([]) })
    }, 240)
    return () => { cancelled = true; clearTimeout(debounce.current) }
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
        <div style={{ fontFamily: MONO, fontSize: 17, fontWeight: 600, color: 'var(--accent)', }}>
          ◬ Research
        </div>
        <div style={{ fontFamily: MONO, fontSize: 12, color: 'var(--ink-3)' }}>
          Global coverage — {indexed ? `${indexed.toLocaleString()} symbols` : 'equities, ETFs, crypto and FX'} across
          40+ exchanges, from NSE and LSE to NYSE and Tokyo. Any other listing resolves on demand.
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
            width: '100%', boxSizing: 'border-box', background: 'var(--inset)',
            border: '1px solid var(--line-2)', color: 'var(--ink)', fontFamily: MONO,
            fontSize: 14, padding: '10px 14px', outline: 'none', borderRadius: 10,
          }} />
        {results.length > 0 && (
          <div style={{
            position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
            background: 'var(--inset)', border: '1px solid var(--line-2)', borderTop: 'none',
            maxHeight: 340, overflowY: 'auto', borderRadius: '0 0 4px 4px',
          }}>
            {results.map((r, i) => (
              <div key={`${r.symbol}-${i}`} onClick={() => choose(r.symbol)}
                style={{
                  display: 'flex', gap: 10, alignItems: 'baseline', padding: '8px 12px',
                  cursor: 'pointer', borderBottom: '1px solid var(--inset)',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--inset)')}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                <span style={{ fontFamily: MONO, fontSize: 14, fontWeight: 600, color: 'var(--accent)', minWidth: 96 }}>{r.symbol}</span>
                <span style={{ fontFamily: MONO, fontSize: 12, color: 'var(--ink-3)', minWidth: 42 }}>{r.exchange}</span>
                <span style={{ fontFamily: MONO, fontSize: 12, color: 'var(--green)', minWidth: 20 }}>{CCY_SYMBOL[r.currency] || ''}</span>
                <span style={{ fontSize: 13, color: 'var(--ink)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
                {r.has_fno ? (
                  <span style={{ fontFamily: MONO, fontSize: 12, color: 'var(--yellow)', border: '1px solid color-mix(in srgb, var(--yellow) 30%, transparent)', borderRadius: 8, padding: '0 4px' }}>
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
          fontFamily: MONO, fontSize: 14, color: 'var(--ink-3)', textAlign: 'center', padding: 54,
          border: '1px dashed var(--inset)', borderRadius: 14,
        }}>
          Search a symbol above — or click any ticker anywhere in ARIA to land here.
        </div>
      )}

      {/* ── ONE DOSSIER, NOT TABS ──
          §4: Discover, Analyse, Compare, Extract and Test are operations of one
          research intelligence, not destinations. This page used to put
          Overview and Deep behind a TabBar, and the V5 engine — the deepest
          layer of all — behind an entirely separate nav entry with its own
          search box. A reader who did not already know V5 existed never found
          the analysis that mattered most.

          Now one search owns the symbol and the depths stack downward: the
          quick dossier, the evidence-linked scoring, then the full ensemble.
          Each layer mounts only once the one above it has a symbol, so opening
          a ticker does not fire forty modules before you have decided to
          look. */}
      {symbol && <ResearchAnalysis key={symbol} symbol={symbol} technicalOnly />}
      {symbol && !isExactGoldContract(symbol) && (
        <>
          <SectionHeader>Overview · {symbol}</SectionHeader>
          <Explorer key={symbol} symbol={symbol} embedded />

          <div style={{ marginTop: 22 }}>
            <SectionHeader>EVIDENCE &amp; SCORING</SectionHeader>
            <Nexus symbol={symbol} embedded />
          </div>

          <div style={{ marginTop: 22 }}>
            <SectionHeader>Full ensemble · 40 modules</SectionHeader>
            <div style={{ fontFamily: MONO, fontSize: 12, color: 'var(--ink-3)', marginBottom: 10 }}>
              No single model speaks alone here, and confidence falls when the
              modules disagree. Abstentions are reported rather than hidden.
            </div>
            {deep ? (
              <V5 symbol={symbol} embedded />
            ) : (
              <button onClick={() => switchTab('deep')} style={{
                fontFamily: MONO, fontSize: 13, fontWeight: 600, padding: '9px 20px',
                cursor: 'pointer', background: 'none', border: '1px solid var(--accent)',
                color: 'var(--accent)', borderRadius: 10,
              }}>
                Run the full ensemble on {symbol}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}
