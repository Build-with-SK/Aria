/**
 * Compare.jsx — Bloomberg-style side-by-side stock decision terminal
 * Answers: "Should I buy X or Y?" in a visual, data-dense layout.
 */

import React, { useState, useEffect } from 'react'
import { RadarChart, PolarGrid, PolarAngleAxis, Radar, ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts'
import { ScoreGauge, ScoreBar, BBCard, SectionHeader, ConflictRow, Spinner, ErrorBox } from '../components/UI'

const DEFAULT_A = 'NVDA'
const DEFAULT_B = 'TSLA'

async function fetchSignal(ticker) {
  const res = await fetch(`/api/signals/${ticker}`)
  if (!res.ok) throw new Error(`${ticker} not found`)
  return res.json()
}

/* ── Sub-component: Verdict Banner ─────────────────────────────────── */
function VerdictBanner({ a, b, nameA, nameB }) {
  const sa = parseFloat(a?.composite_score) || 0
  const sb = parseFloat(b?.composite_score) || 0
  const diff = Math.abs(sa - sb)

  let winner, loser, color, label, detail
  if (diff < 5) {
    winner = null; color = 'var(--yellow)'; label = 'INCONCLUSIVE — WAIT FOR CLEARER SIGNAL'
    detail = `Scores within ${diff.toFixed(1)} pts. No edge. Avoid acting without more conviction.`
  } else if (sa > sb) {
    winner = nameA; loser = nameB; color = 'var(--green)'
    const conf = a?.confidence || '?'
    const arch = a?.situation_archetype || ''
    label = `BUY ${nameA}`
    detail = `${nameA} leads by ${diff.toFixed(1)} pts · ${conf} conviction · ${arch.replace(/_/g,' ')}`
  } else {
    winner = nameB; loser = nameA; color = 'var(--green)'
    const conf = b?.confidence || '?'
    const arch = b?.situation_archetype || ''
    label = `BUY ${nameB}`
    detail = `${nameB} leads by ${diff.toFixed(1)} pts · ${conf} conviction · ${arch.replace(/_/g,' ')}`
  }

  const conflictsA = (a?.conflict_flags || []).filter(c => c.severity === 'HIGH').length
  const conflictsB = (b?.conflict_flags || []).filter(c => c.severity === 'HIGH').length

  return (
    <div style={{
      background: 'var(--surface)',
      border: `1px solid ${color}`,
      borderRadius: 8,
      padding: '16px 20px',
      marginBottom: 16,
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      flexWrap: 'wrap', gap: 12,
    }}>
      <div>
        <div style={{ fontFamily:'var(--font)', fontSize: 26, fontWeight: 600, color, }}>
          {label}
        </div>
        <div style={{ fontFamily:'var(--font)', fontSize: 13, color: 'var(--ink-3)', marginTop: 4 }}>
          {detail}
        </div>
        {(conflictsA > 0 || conflictsB > 0) && (
          <div style={{ fontFamily:'var(--font)', fontSize: 12, color: 'var(--yellow)', marginTop: 6 }}>
            ⚠ {conflictsA} HIGH conflict(s) in {nameA} · {conflictsB} HIGH conflict(s) in {nameB} · Size conservatively
          </div>
        )}
      </div>
      <div style={{ display:'flex', gap: 24 }}>
        <div style={{ textAlign:'center' }}>
          <div style={{ fontFamily:'var(--font)', fontSize: 32, fontWeight: 600, color: sa >= sb ? 'var(--green)' : 'var(--ink-3)' }}>
            {sa > 0 ? '+' : ''}{sa.toFixed(1)}
          </div>
          <div style={{ fontFamily:'var(--font)', fontSize: 12, color:'var(--ink-3)' }}>{nameA} score</div>
        </div>
        <div style={{ fontFamily:'var(--font)', fontSize: 22, color:'var(--ink-3)', alignSelf:'center' }}>vs</div>
        <div style={{ textAlign:'center' }}>
          <div style={{ fontFamily:'var(--font)', fontSize: 32, fontWeight: 600, color: sb > sa ? 'var(--green)' : 'var(--ink-3)' }}>
            {sb > 0 ? '+' : ''}{sb.toFixed(1)}
          </div>
          <div style={{ fontFamily:'var(--font)', fontSize: 12, color:'var(--ink-3)' }}>{nameB} score</div>
        </div>
      </div>
    </div>
  )
}

/* ── Sub-component: Signal Column ───────────────────────────────────── */
function SignalColumn({ ticker, data, color }) {
  if (!data) return (
    <div className="bb-card" style={{ flex:1 }}>
      <Spinner />
    </div>
  )

  const thesis = data.trade_thesis || {}
  const action = data.action || 'Neutral'
  const actionColor = action.includes('Buy') ? 'var(--green)' : action.includes('Sell') ? 'var(--red)' : 'var(--yellow)'
  const archetype = (data.situation_archetype || 'UNKNOWN').replace(/_/g,' ')
  const conflicts = data.conflict_flags || []
  const highConflicts = conflicts.filter(c => c.severity === 'HIGH')

  const sub = [
    { label: 'Trend',     val: data.trend_score },
    { label: 'Momentum',  val: data.momentum_score },
    { label: 'Regime',    val: data.regime_score },
    { label: 'Volatility',val: data.volatility_score },
    { label: 'Macro',     val: data.macro_score },
    { label: 'Sentiment', val: data.sentiment_score },
  ]

  const radarData = sub.map(s => ({ subject: s.label, value: Math.max(0, ((s.val || 0) + 100) / 2) }))

  const price = parseFloat(data.current_price) || 0
  const high52 = parseFloat(data.price_52w_high) || 0
  const low52  = parseFloat(data.price_52w_low) || 0
  const pctFrom52H = high52 ? (((price - high52) / high52) * 100).toFixed(1) : '?'

  return (
    <div style={{ flex:1, display:'flex', flexDirection:'column', gap:10, minWidth: 0 }}>

      {/* Header */}
      <div className="bb-card" style={{ borderColor: color, borderTopWidth: 2 }}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
          <div>
            <div style={{ fontFamily:'var(--font)', fontSize: 26, fontWeight: 600, color: 'var(--ink)', }}>
              {ticker}
            </div>
            <div style={{ fontFamily:'var(--font)', fontSize: 12, color:'var(--ink-3)', marginTop: 2 }}>
              {data.name} · {(data.asset_class || '').toUpperCase()}
            </div>
          </div>
          <div style={{ textAlign:'right' }}>
            <div style={{ fontFamily:'var(--font)', fontSize: 22, fontWeight: 600, color: 'var(--ink)' }}>
              ${price.toLocaleString(undefined, { minimumFractionDigits:2, maximumFractionDigits:2 })}
            </div>
            <div style={{ fontFamily:'var(--font)', fontSize: 12, color: parseFloat(pctFrom52H) < 0 ? 'var(--red)' : 'var(--green)' }}>
              {pctFrom52H}% from 52W HIGH
            </div>
          </div>
        </div>

        {/* Action + Archetype */}
        <div style={{ marginTop: 12, display:'flex', gap: 8, flexWrap:'wrap', alignItems:'center' }}>
          <div style={{
            fontFamily:'var(--font)', fontSize:14, fontWeight:600, color: actionColor,
            background: `color-mix(in srgb, ${actionColor} 10%, transparent)`, border:`1px solid color-mix(in srgb, ${actionColor} 28%, transparent)`,
            padding:'3px 10px', borderRadius:8,
          }}>
            {action.toUpperCase()}
          </div>
          <div style={{
            fontFamily:'var(--font)', fontSize:12, color: color,
            background:`color-mix(in srgb, ${color} 8%, transparent)`, border:`1px solid color-mix(in srgb, ${color} 20%, transparent)`,
            padding:'3px 10px', borderRadius:8,
          }}>
            {archetype}
          </div>
          <div style={{
            fontFamily:'var(--font)', fontSize:12,
            color: data.confidence === 'High' ? 'var(--green)' : data.confidence === 'Low' ? 'var(--red)' : 'var(--yellow)',
          }}>
            {data.confidence?.toUpperCase()} conviction
          </div>
        </div>
      </div>

      {/* Score gauge + sub-scores */}
      <div className="bb-card">
        <div style={{ display:'flex', gap:16, alignItems:'flex-start' }}>
          <div style={{ display:'flex', flexDirection:'column', alignItems:'center' }}>
            <ScoreGauge score={data.composite_score} size={130} />
            <div style={{ fontFamily:'var(--font)', fontSize:12, color:'var(--ink-3)', marginTop:-4 }}>Composite score</div>
          </div>
          <div style={{ flex:1 }}>
            {sub.map(s => <ScoreBar key={s.label} score={s.val} label={s.label} />)}
          </div>
        </div>
      </div>

      {/* Radar chart */}
      <div className="bb-card">
        <div className="bb-card-header" style={{ color }}>Signal composition</div>
        <ResponsiveContainer width="100%" height={160}>
          <RadarChart data={radarData} margin={{ top:4, right:16, bottom:4, left:16 }}>
            <PolarGrid stroke="var(--inset)" />
            <PolarAngleAxis dataKey="subject" tick={{ fill:'var(--ink-3)', fontSize:12, fontFamily:'var(--font)' }} />
            <Radar dataKey="value" stroke={color} fill={color} fillOpacity={0.15} strokeWidth={1.5} />
          </RadarChart>
        </ResponsiveContainer>
      </div>

      {/* Trade thesis */}
      {thesis.bull_case && (
        <div className="bb-card">
          <div className="bb-card-header" style={{ color:'var(--green)' }}>Bull case</div>
          {(thesis.bull_case || []).slice(0,4).map((b, i) => (
            <div key={i} style={{ display:'flex', gap:6, marginBottom:6 }}>
              <span style={{ color:'var(--green)', fontFamily:'var(--font)', fontSize:12, flexShrink:0 }}>▲</span>
              <span style={{ fontFamily:'var(--font)', fontSize:13, color:'var(--ink)', lineHeight:1.4 }}>{b}</span>
            </div>
          ))}
        </div>
      )}

      {thesis.bear_case && (
        <div className="bb-card">
          <div className="bb-card-header" style={{ color:'var(--red)' }}>Bear case / risks</div>
          {(thesis.bear_case || []).slice(0,4).map((b, i) => (
            <div key={i} style={{ display:'flex', gap:6, marginBottom:6 }}>
              <span style={{ color:'var(--red)', fontFamily:'var(--font)', fontSize:12, flexShrink:0 }}>▼</span>
              <span style={{ fontFamily:'var(--font)', fontSize:13, color:'var(--ink)', lineHeight:1.4 }}>{b}</span>
            </div>
          ))}
        </div>
      )}

      {/* Invalidation */}
      {thesis.invalidation?.length > 0 && (
        <div className="bb-card" style={{ borderColor:'var(--yellow-bg)' }}>
          <div className="bb-card-header" style={{ color:'var(--yellow)' }}>Invalidation triggers</div>
          {thesis.invalidation.slice(0,3).map((inv, i) => (
            <div key={i} style={{ display:'flex', gap:6, marginBottom:5 }}>
              <span style={{ color:'var(--yellow)', fontFamily:'var(--font)', fontSize:12, flexShrink:0 }}>!</span>
              <span style={{ fontFamily:'var(--font)', fontSize:13, color:'var(--ink-3)', lineHeight:1.4 }}>{inv}</span>
            </div>
          ))}
        </div>
      )}

      {/* Risk params */}
      <div className="bb-card">
        <div className="bb-card-header">Risk parameters</div>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8 }}>
          {[
            ['STOP LOSS',    `$${parseFloat(data.stop_loss||0).toFixed(2)}`,    'var(--red)'],
            ['TAKE PROFIT',  `$${parseFloat(data.take_profit||0).toFixed(2)}`,  'var(--green)'],
            ['POSITION SIZE',`${(data.position_size_pct||0).toFixed(1)}%`,      color],
            ['REALISED VOL', `${((data.realised_vol||0)*100).toFixed(1)}%`,     'var(--ink-3)'],
            ['ATR %',        `${((data.atr_pct||0)*100).toFixed(2)}%`,          'var(--ink-3)'],
            ['RISK LEVEL',   data.risk_level,
              data.risk_level === 'Very High' ? 'var(--red)' : data.risk_level === 'High' ? 'var(--yellow)' : 'var(--green)'],
          ].map(([lbl, val, c]) => (
            <div key={lbl} style={{ background:'var(--inset)', padding:'8px 10px', borderRadius:8 }}>
              <div style={{ fontFamily:'var(--font)', fontSize:12, color:'var(--ink-3)', marginBottom:3, }}>{lbl}</div>
              <div style={{ fontFamily:'var(--font)', fontSize:14, fontWeight:600, color: c || 'var(--ink)' }}>{val}</div>
            </div>
          ))}
        </div>
      </div>

      {/* 52W range */}
      <div className="bb-card">
        <div className="bb-card-header">52-Week range</div>
        <div style={{ display:'flex', justifyContent:'space-between', marginBottom:6 }}>
          <span style={{ fontFamily:'var(--font)', fontSize:13, color:'var(--red)' }}>${low52.toFixed(2)}</span>
          <span style={{ fontFamily:'var(--font)', fontSize:13, color:'var(--ink)' }}>${price.toFixed(2)}</span>
          <span style={{ fontFamily:'var(--font)', fontSize:13, color:'var(--green)' }}>${high52.toFixed(2)}</span>
        </div>
        <div style={{ background:'var(--inset)', borderRadius:8, height:8, position:'relative' }}>
          {high52 > low52 && (
            <div style={{
              position:'absolute', left:0, top:0, height:'100%',
              width:`${((price - low52) / (high52 - low52)) * 100}%`,
              background: `linear-gradient(to right, var(--red-dim), ${color})`,
              borderRadius:8, maxWidth:'100%',
            }} />
          )}
        </div>
      </div>

      {/* Signal conflicts */}
      {conflicts.length > 0 && (
        <div className="bb-card" style={{ borderColor: highConflicts.length > 0 ? 'var(--red-bg)' : 'var(--yellow-bg)' }}>
          <div className="bb-card-header" style={{ color:'var(--yellow)' }}>
            Signal conflicts ({conflicts.length}) {highConflicts.length > 0 && `· ${highConflicts.length} High`}
          </div>
          {conflicts.slice(0,3).map((c, i) => <ConflictRow key={i} conflict={c} />)}
          {data.conflict_penalty > 0 && (
            <div style={{ fontFamily:'var(--font)', fontSize:12, color:'var(--ink-3)', marginTop:6 }}>
              Score penalised by {data.conflict_penalty?.toFixed(1)} pts due to conflicts
            </div>
          )}
        </div>
      )}

      {/* Sizing rationale */}
      {thesis.sizing_rationale && (
        <div className="bb-card" style={{ borderColor:'var(--blue-bg)' }}>
          <div className="bb-card-header" style={{ color:'var(--blue)' }}>Position sizing</div>
          <div style={{ fontFamily:'var(--font)', fontSize:13, color:'var(--ink)', lineHeight:1.5 }}>
            {thesis.sizing_rationale}
          </div>
        </div>
      )}

    </div>
  )
}

/* ── Head-to-head comparison table ──────────────────────────────────── */
function HeadToHead({ a, b, nameA, nameB }) {
  const rows = [
    ['Composite Score',  a?.composite_score?.toFixed(1), b?.composite_score?.toFixed(1), true],
    ['Action',           a?.action,                      b?.action,                      false],
    ['Confidence',       a?.confidence,                  b?.confidence,                  false],
    ['Bull Probability', `${((a?.bullish_prob||0)*100).toFixed(0)}%`, `${((b?.bullish_prob||0)*100).toFixed(0)}%`, true],
    ['Regime',           a?.regime,                      b?.regime,                      false],
    ['Situation',        (a?.situation_archetype||'').replace(/_/g,' '), (b?.situation_archetype||'').replace(/_/g,' '), false],
    ['Realised Vol',     `${((a?.realised_vol||0)*100).toFixed(1)}%`, `${((b?.realised_vol||0)*100).toFixed(1)}%`, false, true],
    ['Risk Level',       a?.risk_level,                  b?.risk_level,                  false],
    ['Position Size',    `${(a?.position_size_pct||0).toFixed(1)}%`, `${(b?.position_size_pct||0).toFixed(1)}%`, true],
    ['Conflicts',        `${(a?.conflict_flags||[]).length}`, `${(b?.conflict_flags||[]).length}`, false, true],
    ['Conflict Penalty', `${(a?.conflict_penalty||0).toFixed(1)}`, `${(b?.conflict_penalty||0).toFixed(1)}`, false, true],
  ]

  return (
    <div className="bb-card" style={{ marginBottom: 16 }}>
      <div className="bb-card-header">Head-to-head comparison</div>
      <table>
        <thead>
          <tr>
            <th>Metric</th>
            <th style={{ color:'var(--accent)' }}>{nameA}</th>
            <th style={{ color:'var(--blue)' }}>{nameB}</th>
            <th>Edge</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, va, vb, numeric, lowerBetter]) => {
            let edge = '—'
            let edgeColor = 'var(--ink-3)'
            if (numeric && va !== undefined && vb !== undefined) {
              const na = parseFloat(va), nb = parseFloat(vb)
              if (!isNaN(na) && !isNaN(nb)) {
                const aBetter = lowerBetter ? na < nb : na > nb
                edge = aBetter ? nameA : nb !== na ? nameB : '—'
                edgeColor = aBetter ? 'var(--accent)' : nb !== na ? 'var(--blue)' : 'var(--ink-3)'
              }
            }
            return (
              <tr key={label}>
                <td style={{ color:'var(--ink-3)', fontSize:13 }}>{label}</td>
                <td style={{ color:'var(--accent)', fontWeight:600 }}>{va ?? '—'}</td>
                <td style={{ color:'var(--blue)', fontWeight:600 }}>{vb ?? '—'}</td>
                <td style={{ color: edgeColor, fontWeight:600, fontSize:13 }}>{edge}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/* ── Main page ───────────────────────────────────────────────────────── */
export default function Compare() {
  const [tickerA, setTickerA] = useState(DEFAULT_A)
  const [tickerB, setTickerB] = useState(DEFAULT_B)
  const [inputA, setInputA] = useState(DEFAULT_A)
  const [inputB, setInputB] = useState(DEFAULT_B)
  const [dataA, setDataA] = useState(null)
  const [dataB, setDataB] = useState(null)
  const [loadingA, setLoadingA] = useState(false)
  const [loadingB, setLoadingB] = useState(false)
  const [errA, setErrA] = useState(null)
  const [errB, setErrB] = useState(null)
  const [time, setTime] = useState(new Date())

  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(t)
  }, [])

  const loadTicker = async (ticker, setter, setLoading, setErr) => {
    setLoading(true); setErr(null)
    try {
      const d = await fetchSignal(ticker.toUpperCase().trim())
      setter(d)
    } catch (e) {
      setErr(e.message)
      setter(null)
    } finally { setLoading(false) }
  }

  useEffect(() => { loadTicker(tickerA, setDataA, setLoadingA, setErrA) }, [tickerA])
  useEffect(() => { loadTicker(tickerB, setDataB, setLoadingB, setErrB) }, [tickerB])

  const handleCompare = () => {
    setTickerA(inputA.toUpperCase().trim())
    setTickerB(inputB.toUpperCase().trim())
  }

  return (
    <div>
      {/* Top bar */}
      <div style={{
        display:'flex', alignItems:'center', justifyContent:'space-between',
        marginBottom: 14, gap: 12, flexWrap:'wrap',
      }}>
        <div>
          <div style={{ fontFamily:'var(--font)', fontSize:15, fontWeight:600, color:'var(--accent)', }}>
            ⚡ Stock comparison terminal
          </div>
          <div style={{ fontFamily:'var(--font)', fontSize:12, color:'var(--ink-3)', marginTop:2 }}>
            {time.toLocaleTimeString('en-US', { hour12:false })} · ARIA signal engine · situation-aware analysis
          </div>
        </div>

        {/* Ticker inputs */}
        <div style={{ display:'flex', gap:8, alignItems:'center' }}>
          <input
            value={inputA}
            onChange={e => setInputA(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && handleCompare()}
            style={{
              background:'var(--surface)', border:'1px solid var(--accent)', borderRadius:8,
              padding:'7px 10px', color:'var(--accent)', fontFamily:'var(--font)',
              fontSize:15, fontWeight:600, width:90, textAlign:'center', 
            }}
          />
          <span style={{ fontFamily:'var(--font)', color:'var(--ink-3)', fontSize:14 }}>vs</span>
          <input
            value={inputB}
            onChange={e => setInputB(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && handleCompare()}
            style={{
              background:'var(--surface)', border:'1px solid var(--blue)', borderRadius:8,
              padding:'7px 10px', color:'var(--blue)', fontFamily:'var(--font)',
              fontSize:15, fontWeight:600, width:90, textAlign:'center', 
            }}
          />
          <button
            onClick={handleCompare}
            style={{
              background:'var(--accent)', color:'var(--on-solid)', border:'none', borderRadius:8,
              padding:'7px 16px', fontFamily:'var(--font)', fontSize:13, fontWeight:600,
              cursor:'pointer', 
            }}>
            Analyze
          </button>
        </div>
      </div>

      {/* Verdict banner */}
      {(dataA || dataB) && !loadingA && !loadingB && (
        <VerdictBanner a={dataA} b={dataB} nameA={tickerA} nameB={tickerB} />
      )}

      {/* Loading states */}
      {(loadingA || loadingB) && (
        <div className="bb-card" style={{ marginBottom:14 }}>
          <Spinner />
        </div>
      )}

      {/* Head to head */}
      {dataA && dataB && !loadingA && !loadingB && (
        <HeadToHead a={dataA} b={dataB} nameA={tickerA} nameB={tickerB} />
      )}

      {/* Side by side columns */}
      <div style={{ display:'flex', gap:12, alignItems:'flex-start' }}>

        {/* Column A */}
        <div style={{ flex:1, minWidth:0 }}>
          <div style={{
            fontFamily:'var(--font)', fontSize:13, fontWeight:600, color:'var(--accent)',
            textAlign:'center', marginBottom:8,
            padding:'5px', background:'var(--accent-bg)', border:'1px solid var(--line)',
          }}>
            ── {tickerA} ──
          </div>
          {errA && <div style={{ fontFamily:'var(--font)', fontSize:13, color:'var(--red)', marginBottom:8 }}>⚠ {errA}</div>}
          {!loadingA && (
            <SignalColumn ticker={tickerA} data={dataA} color="var(--accent)" />
          )}
        </div>

        {/* Divider */}
        <div style={{ width:1, background:'var(--line-2)', alignSelf:'stretch', flexShrink:0 }} />

        {/* Column B */}
        <div style={{ flex:1, minWidth:0 }}>
          <div style={{
            fontFamily:'var(--font)', fontSize:13, fontWeight:600, color:'var(--blue)',
            textAlign:'center', marginBottom:8,
            padding:'5px', background:'color-mix(in srgb, var(--blue) 6%, transparent)', border:'1px solid var(--line)',
          }}>
            ── {tickerB} ──
          </div>
          {errB && <div style={{ fontFamily:'var(--font)', fontSize:13, color:'var(--red)', marginBottom:8 }}>⚠ {errB}</div>}
          {!loadingB && (
            <SignalColumn ticker={tickerB} data={dataB} color="var(--blue)" />
          )}
        </div>
      </div>

      {/* Disclaimer */}
      <div style={{ marginTop:24, fontFamily:'var(--font)', fontSize:12, color:'var(--ink-3)', textAlign:'center', lineHeight:1.6 }}>
        ⚠ ARIA is a research tool. All signals are probabilistic and not financial advice.
        Past signal accuracy does not guarantee future results. Trade at your own risk.
      </div>
    </div>
  )
}
