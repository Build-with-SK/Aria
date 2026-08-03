/**
 * components/ResearchAnalysis.jsx
 * Technical and fundamental analysis for one symbol, computed on demand.
 *
 * Not a second opinion bolted onto the research page — this is the same V5
 * engine the whole platform runs on, filtered to the families that answer each
 * question. Every module shows its view, its confidence INTERVAL, the evidence
 * it cited and the source of that evidence, so a reader can check the work
 * rather than take the verdict.
 *
 *   Technical   price + volatility families, plus the multi-timeframe
 *               indicator consensus and the risk gate's trade levels
 *   Fundamental value / growth / quality / earnings / factor, peer-relative
 *               where a peer set loads, with the raw ratios underneath
 *
 * Nothing here is cached server-side beyond the data layer's own TTLs: both
 * panels are computed when you open the symbol.
 */
import React, { useCallback, useEffect, useState } from 'react'
import axios from 'axios'
import { useCurrency } from '../currency/CurrencyContext'
import Term from './Term'

const MONO = 'var(--mono)'
const LABEL_COLOR = {
  'Strong Buy': 'var(--green)', Buy: '#7fd18b', Neutral: 'var(--muted)',
  Sell: '#e88', 'Strong Sell': 'var(--red)',
}
const VIEW_COLOR = { bull: 'var(--green)', bear: 'var(--red)', neutral: 'var(--muted)', abstain: '#4a4a55' }
const pct = (x, d = 0) => (x == null ? '—' : `${(x * 100).toFixed(d)}%`)

function Mono({ children, size = 11, color = 'var(--muted)', weight = 400, style }) {
  return <span style={{ fontFamily: MONO, fontSize: size, color, fontWeight: weight, ...style }}>{children}</span>
}

function Meter({ counts, width = 96 }) {
  if (!counts) return null
  const { buy = 0, sell = 0, neutral = 0 } = counts
  const total = buy + sell + neutral || 1
  const seg = (n, c) => (n ? <div style={{ width: `${(n / total) * 100}%`, background: c, height: 6 }} /> : null)
  return (
    <div style={{ display: 'flex', width, height: 6, borderRadius: 3, overflow: 'hidden', border: '1px solid var(--border)' }}>
      {seg(buy, 'var(--green)')}{seg(neutral, '#2a2a34')}{seg(sell, 'var(--red)')}
    </div>
  )
}

/* one module: verdict, interval, and the evidence behind it */
function ModuleCard({ m }) {
  const [open, setOpen] = useState(false)
  const col = VIEW_COLOR[m.view] || 'var(--muted)'
  const abstained = m.insufficient_data
  return (
    <div style={{
      border: '1px solid var(--border)', borderRadius: 5, padding: '9px 11px',
      marginBottom: 7, background: 'rgba(255,255,255,.012)', opacity: abstained ? 0.62 : 1,
    }}>
      <div onClick={() => setOpen(o => !o)} style={{ cursor: 'pointer', display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <Mono size={11} color="var(--text)" weight={700} style={{ minWidth: 150 }}>{m.module}</Mono>
        <Mono size={10} color={col} weight={800} style={{ minWidth: 58 }}>{(m.view || '').toUpperCase()}</Mono>
        {!abstained && (
          <>
            <Mono size={11} color={col} weight={700} style={{ minWidth: 44 }}>
              {m.net > 0 ? '+' : ''}{Number(m.net).toFixed(0)}
            </Mono>
            <Mono size={9}>
              {m.ci_low != null ? `CI ${pct(m.ci_low)}–${pct(m.ci_high)}` : 'no interval'}
              {m.n_obs ? ` · n=${m.n_obs}` : ''}
            </Mono>
          </>
        )}
        <Mono size={9} style={{ marginLeft: 'auto' }}>{open ? '▲ hide' : '▼ evidence'}</Mono>
      </div>
      <div style={{ color: abstained ? 'var(--muted)' : 'var(--text)', fontSize: 11, lineHeight: 1.55, marginTop: 5 }}>
        {abstained ? m.reason : m.thesis}
      </div>

      {open && !abstained && (
        <div style={{ marginTop: 8 }}>
          {(m.evidence || []).map((e, i) => (
            <div key={i} style={{ borderLeft: `2px solid ${VIEW_COLOR[e.lean] || '#2a2a34'}`, paddingLeft: 8, marginBottom: 6 }}>
              <div style={{ color: 'var(--text)', fontSize: 11 }}>{e.claim}</div>
              <Mono size={8.5}>source: {e.source}</Mono>
            </div>
          ))}
          {!!(m.weaknesses || []).filter(Boolean).length && (
            <div style={{ marginTop: 7 }}>
              <Mono size={9} color="var(--red)" weight={800}>WHAT THIS MODULE GETS WRONG</Mono>
              {m.weaknesses.filter(Boolean).map((w, i) => (
                <div key={i} style={{ color: 'var(--muted)', fontSize: 10.5, marginTop: 3 }}>— {w}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FamilyVerdict({ modules, title }) {
  const voting = modules.filter(m => !m.insufficient_data)
  if (!voting.length) {
    return <Mono size={11}>No {title} module could report on this instrument.</Mono>
  }
  const net = voting.reduce((s, m) => s + Number(m.net || 0), 0) / voting.length
  const bulls = voting.filter(m => m.view === 'bull').length
  const bears = voting.filter(m => m.view === 'bear').length
  const col = net > 5 ? 'var(--green)' : net < -5 ? 'var(--red)' : 'var(--muted)'
  const word = net > 5 ? 'BULLISH' : net < -5 ? 'BEARISH' : 'MIXED / NEUTRAL'
  return (
    <div style={{ display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
      <div>
        <Mono size={9}>{title.toUpperCase()} VERDICT</Mono>
        <div style={{ fontFamily: MONO, fontSize: 17, fontWeight: 800, color: col }}>{word}</div>
      </div>
      <div>
        <Mono size={9}>AVERAGE NET</Mono>
        <div style={{ fontFamily: MONO, fontSize: 15, color: col }}>{net > 0 ? '+' : ''}{net.toFixed(1)}</div>
      </div>
      <div>
        <Mono size={9}>AGREEMENT</Mono>
        <div style={{ fontFamily: MONO, fontSize: 13, color: 'var(--text)' }}>
          {bulls} bull · {bears} bear · {voting.length - bulls - bears} neutral
        </div>
      </div>
      <Meter counts={{ buy: bulls, sell: bears, neutral: voting.length - bulls - bears }} width={110} />
    </div>
  )
}

/* ── multi-timeframe indicator consensus ── */
function TimeframeConsensus({ tech }) {
  if (!tech) return <Mono size={11}>loading indicator consensus…</Mono>
  const tfs = tech.timeframes || {}
  const order = ['5m', '15m', '1h', '1d', '1wk']
  const rows = order.filter(t => tfs[t])
  if (!rows.length) return <Mono size={11}>No timeframe data available for this instrument.</Mono>
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', marginBottom: 8, flexWrap: 'wrap' }}>
        <Mono size={9}>CONSENSUS ACROSS TIMEFRAMES</Mono>
        <Mono size={13} color={LABEL_COLOR[tech.consensus] || 'var(--muted)'} weight={800}>
          {tech.consensus || '—'}
        </Mono>
      </div>
      <table>
        <thead><tr><th>TIMEFRAME</th><th>SIGNAL</th><th>BUY / NEU / SELL</th><th></th></tr></thead>
        <tbody>
          {rows.map(t => {
            const d = tfs[t]
            if (d.error) return (
              <tr key={t}><td><Mono size={10} color="var(--text)">{t}</Mono></td>
                <td colSpan={3}><Mono size={9}>{d.error}</Mono></td></tr>
            )
            return (
              <tr key={t}>
                <td><Mono size={10} color="var(--text)" weight={700}>{t}</Mono></td>
                <td><Mono size={10} color={LABEL_COLOR[d.summary] || 'var(--muted)'} weight={800}>{d.summary}</Mono></td>
                <td><Meter counts={d.counts} /></td>
                <td><Mono size={9}>{d.counts?.buy}/{d.counts?.neutral}/{d.counts?.sell}</Mono></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/* ── indicator-by-indicator, for the daily ── */
function IndicatorGrid({ detail }) {
  if (!detail || detail.error) return null
  const groups = [['MOVING AVERAGES', detail.moving_averages], ['OSCILLATORS', detail.oscillators]]
  const sig = v => v === 'buy' ? 'var(--green)' : v === 'sell' ? 'var(--red)' : 'var(--muted)'
  return (
    <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginTop: 12 }}>
      {groups.map(([title, g]) => {
        if (!g) return null
        const items = Object.entries(g).filter(([k]) => k !== 'label')
        return (
          <div key={title} style={{ minWidth: 210 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', marginBottom: 5 }}>
              <Mono size={9} color="var(--orange)" weight={800}>{title}</Mono>
              <Mono size={10} color={LABEL_COLOR[g.label] || 'var(--muted)'} weight={800}>{g.label}</Mono>
            </div>
            {items.map(([k, v]) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '1.5px 0' }}>
                <Mono size={10} color="var(--text)">{k}</Mono>
                <Mono size={10} color={sig(v)} weight={700}>{String(v).toUpperCase()}</Mono>
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}

/* ── the levels a trade would actually use ── */
function TradeLevels({ v5, symbol }) {
  const { price } = useCurrency()
  const rec = v5?.recommendation
  const risk = v5?.risk
  if (!rec || !risk) return null
  const p = v => price(v, { symbol }).text
  const cell = (label, value, color) => (
    <div key={typeof label === 'string' ? label : undefined}>
      <Mono size={9}>{label}</Mono>
      <div style={{ fontFamily: MONO, fontSize: 14, color: color || 'var(--text)', fontWeight: 700 }}>{value}</div>
    </div>
  )
  const sized = Number(rec.position_size_pct || 0) > 0
  const verdictColor = { APPROVE: 'var(--green)', REDUCE: 'var(--orange)' }[risk.verdict] || 'var(--muted)'

  return (
    <div style={{ border: '1px solid var(--border-2)', borderRadius: 5, padding: 11, marginBottom: 12 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', marginBottom: 9, flexWrap: 'wrap' }}>
        <Mono size={9}><Term k="risk gate">RISK GATE</Term></Mono>
        <Mono size={12} color={verdictColor} weight={800}>{risk.verdict}</Mono>
        <Mono size={10}>
          confidence {rec.confidence_band} · direction {rec.direction}
        </Mono>
      </div>
      <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap', alignItems: 'baseline' }}>
        {cell('ENTRY', p(rec.entry))}
        {cell(<Term k="stop loss">STOP LOSS</Term>, p(rec.stop), 'var(--red)')}
        {cell(<Term k="target">TARGET</Term>, p(rec.target), 'var(--green)')}
        {cell(<Term k="reward : risk">REWARD : RISK</Term>, risk.reward_risk ? `${Number(risk.reward_risk).toFixed(1)} : 1` : '—')}
        {cell('SUGGESTED SIZE', sized ? `${Number(rec.position_size_pct).toFixed(2)}% of equity` : 'ZERO',
              sized ? undefined : 'var(--muted)')}
        {sized && cell('IF STOPPED', `−${Number(rec.max_loss_pct_of_equity || 0).toFixed(2)}% of equity`, 'var(--red)')}
      </div>
      {/* A zero size next to a stop and target reads like a bug unless the
          reason is on screen. It is the risk gate refusing, not a missing number. */}
      {!sized && (
        <div style={{ color: 'var(--orange)', fontSize: 11, marginTop: 8, lineHeight: 1.55 }}>
          Size is zero: the risk gate returned {risk.verdict} at {rec.confidence_band} confidence.
          The levels above are what a trade WOULD use if the setup earned one — they are reference
          geometry, not a recommendation to enter.
        </div>
      )}
      <div style={{ marginTop: 9, paddingTop: 8, borderTop: '1px solid var(--border)' }}>
        <Mono size={9} color="var(--orange)" weight={800}>INVALIDATION — WHAT WOULD PROVE THIS WRONG</Mono>
        <div style={{ color: 'var(--text)', fontSize: 11, marginTop: 3, lineHeight: 1.55 }}>{rec.invalidation}</div>
      </div>
      <div style={{ marginTop: 7 }}>
        <Mono size={9}>
          Stop is 2×ATR from entry and the target is 2R; size is scaled by the ensemble's edge and
          capped by the desk's risk limits. Levels are a proposal — nothing here places an order.
        </Mono>
      </div>
    </div>
  )
}

/* ── fundamental ratios, straight from the dossier ── */
function RatioGrid({ dossier, currencyOf }) {
  const f = dossier?.fundamentals || {}
  if (!Object.keys(f).length) return null
  const money = v => (v == null ? '—' : Math.abs(v) >= 1e9 ? `${(v / 1e9).toFixed(2)}B`
    : Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : Number(v).toLocaleString())
  const p1 = v => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)
  const n2 = v => (v == null ? '—' : Number(v).toFixed(2))
  const rows = [
    ['Market cap', money(f.marketCap)], ['Trailing P/E', n2(f.trailingPE)],
    ['Forward P/E', n2(f.forwardPE)], ['Price / book', n2(f.priceToBook)],
    ['Return on equity', p1(f.returnOnEquity)], ['Net margin', p1(f.profitMargins)],
    ['Operating margin', p1(f.operatingMargins)], ['Revenue growth', p1(f.revenueGrowth)],
    ['Earnings growth', p1(f.earningsGrowth)], ['Debt / equity', n2(f.debtToEquity)],
    ['Free cash flow', money(f.freeCashflow)], ['Total cash', money(f.totalCash)],
    ['Total debt', money(f.totalDebt)], ['Beta', n2(f.beta)],
    ['52w high', n2(f.fiftyTwoWeekHigh)], ['52w low', n2(f.fiftyTwoWeekLow)],
  ]
  return (
    <div style={{ marginTop: 12 }}>
      <Mono size={9} color="var(--orange)" weight={800}>REPORTED FIGURES</Mono>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: '7px 16px', marginTop: 6 }}>
        {rows.map(([k, v]) => (
          <div key={k}>
            <Mono size={8.5}>{k.toUpperCase()}</Mono>
            <div style={{ fontFamily: MONO, fontSize: 12, color: 'var(--text)' }}>{v}</div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 6 }}>
        <Mono size={8.5}>
          Source: Yahoo Finance fundamentals, unaudited and quarter-lagged. Currency of the
          reported figures is {currencyOf || 'the instrument’s own'}; ratios are unit-free.
        </Mono>
      </div>
    </div>
  )
}

/* Staged progress. A single line of text for eight seconds reads as a hang;
   naming the stage as it passes makes the wait legible and honest — these are
   the steps the backend genuinely runs, in order. */
function AnalysisProgress() {
  const STEPS = [
    'Fetching live prices across five timeframes',
    'Scoring 22 moving averages and oscillators',
    'Running the price and volatility engines',
    'Running the fundamental engines against peers',
    'Synthesising the ensemble and testing it against itself',
    'Sizing through the risk gate',
  ]
  const [i, setI] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setI(v => Math.min(v + 1, STEPS.length - 1)), 1400)
    return () => clearInterval(t)
  }, [])
  return (
    <div aria-busy="true" aria-live="polite" style={{ padding: '4px 2px' }}>
      {STEPS.map((label, k) => {
        const done = k < i, active = k === i
        return (
          <div key={k} style={{
            display: 'flex', alignItems: 'center', gap: 9, padding: '3px 0',
            opacity: done ? 0.55 : active ? 1 : 0.28, transition: 'opacity .3s ease',
          }}>
            <span style={{
              fontFamily: MONO, fontSize: 10, width: 13, textAlign: 'center',
              color: done ? 'var(--green)' : active ? 'var(--orange)' : 'var(--muted)',
            }}>{done ? '✓' : active ? '▸' : '·'}</span>
            <span style={{ fontFamily: MONO, fontSize: 11, color: active ? 'var(--text)' : 'var(--muted)' }}>
              {label}
            </span>
          </div>
        )
      })}
      <div style={{ marginTop: 10, height: 3, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{
          height: '100%', width: `${((i + 1) / STEPS.length) * 100}%`,
          background: 'var(--orange)', transition: 'width .5s cubic-bezier(.22,.7,.3,1)',
        }} />
      </div>
      <div style={{ marginTop: 6 }}>
        <Mono size={9}>Live data, computed now — typically five to ten seconds.</Mono>
      </div>
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════════════ */

export default function ResearchAnalysis({ symbol, dossier, currency }) {
  const [tech, setTech] = useState(null)
  const [detail, setDetail] = useState(null)
  const [v5, setV5] = useState(null)
  const [state, setState] = useState('idle')   // idle | loading | ready | error
  const [err, setErr] = useState('')

  const run = useCallback(() => {
    if (!symbol) return
    setState('loading'); setErr(''); setTech(null); setV5(null); setDetail(null)

    axios.get('/api/technical/summary', { params: { symbol, timeframes: '5m,15m,1h,1d,1wk' } })
      .then(r => setTech(r.data)).catch(() => setTech({ error: 'timeframe scan unavailable' }))

    axios.get('/api/technical/summary', { params: { symbol, timeframes: '1d' } })
      .then(r => {
        // multi_timeframe returns per-timeframe summaries; the per-indicator
        // detail comes from the single-timeframe shape.
        const d = r.data?.timeframes?.['1d']
        setDetail(d && !d.error ? { ...d } : null)
      }).catch(() => {})

    // One V5 pass gives every family plus the risk gate's levels.
    axios.get(`/api/v5/analyze/${encodeURIComponent(symbol)}`, { params: { log: false } })
      .then(r => { setV5(r.data); setState('ready') })
      .catch(e => { setErr(e?.response?.data?.detail || 'analysis failed'); setState('error') })
  }, [symbol])

  useEffect(() => { setState('idle'); setTech(null); setV5(null); setDetail(null) }, [symbol])

  const modules = v5?.modules || []
  const technical = modules.filter(m => ['price', 'volatility'].includes(m.family))
  const fundamental = modules.filter(m => m.family === 'fundamental')

  return (
    <>
      {/* ── TECHNICAL ── */}
      <div className="bb-card">
        <div className="bb-card-header">TECHNICAL ANALYSIS</div>
        {state === 'idle' && (
          <div style={{ padding: '10px 2px' }}>
            <button onClick={run} style={{
              fontFamily: MONO, fontSize: 11, fontWeight: 700, padding: '7px 16px', cursor: 'pointer',
              background: 'none', border: '1px solid var(--orange)', color: 'var(--orange)', borderRadius: 4,
            }}>RUN FULL ANALYSIS ON {symbol}</button>
            <div style={{ marginTop: 7 }}>
              <Mono size={10}>
                Runs the multi-timeframe indicator scan and ARIA&apos;s price, volatility and
                fundamental engines against live data. Takes a few seconds.
              </Mono>
            </div>
          </div>
        )}
        {state === 'loading' && <AnalysisProgress />}
        {state === 'error' && <div style={{ color: 'var(--red)', fontFamily: MONO, fontSize: 11 }}>{err}</div>}

        {state === 'ready' && <>
          <FamilyVerdict modules={technical} title="Technical" />
          <TradeLevels v5={v5} symbol={symbol} />
          <TimeframeConsensus tech={tech} />
          <IndicatorGrid detail={detail} />
          <div style={{ marginTop: 14 }}>
            <Mono size={9} color="var(--orange)" weight={800}>ENGINE-BY-ENGINE — CLICK ANY FOR ITS EVIDENCE</Mono>
            <div style={{ marginTop: 7 }}>
              {technical.map(m => <ModuleCard key={m.module} m={m} />)}
            </div>
          </div>
        </>}
      </div>

      {/* ── FUNDAMENTAL ── */}
      {state === 'ready' && (
        <div className="bb-card">
          <div className="bb-card-header">FUNDAMENTAL ANALYSIS</div>
          <FamilyVerdict modules={fundamental} title="Fundamental" />
          <div style={{ marginTop: 4 }}>
            {fundamental.map(m => <ModuleCard key={m.module} m={m} />)}
          </div>
          <RatioGrid dossier={dossier} currencyOf={currency} />
        </div>
      )}
    </>
  )
}
