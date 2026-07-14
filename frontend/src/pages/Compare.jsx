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
      background: 'var(--bg-2)',
      border: `1px solid ${color}`,
      borderRadius: 2,
      padding: '16px 20px',
      marginBottom: 16,
      boxShadow: `0 0 20px ${color}22`,
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      flexWrap: 'wrap', gap: 12,
    }}>
      <div>
        <div style={{ fontFamily:'var(--mono)', fontSize: 22, fontWeight: 700, color, letterSpacing: '0.08em' }}>
          {label}
        </div>
        <div style={{ fontFamily:'var(--mono)', fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>
          {detail}
        </div>
        {(conflictsA > 0 || conflictsB > 0) && (
          <div style={{ fontFamily:'var(--mono)', fontSize: 10, color: 'var(--yellow)', marginTop: 6 }}>
            ⚠ {conflictsA} HIGH conflict(s) in {nameA} · {conflictsB} HIGH conflict(s) in {nameB} · Size conservatively
          </div>
        )}
      </div>
      <div style={{ display:'flex', gap: 24 }}>
        <div style={{ textAlign:'center' }}>
          <div style={{ fontFamily:'var(--mono)', fontSize: 28, fontWeight: 700, color: sa >= sb ? 'var(--green)' : 'var(--muted)' }}>
            {sa > 0 ? '+' : ''}{sa.toFixed(1)}
          </div>
          <div style={{ fontFamily:'var(--mono)', fontSize: 10, color:'var(--muted)' }}>{nameA} SCORE</div>
        </div>
        <div style={{ fontFamily:'var(--mono)', fontSize: 20, color:'var(--border-2)', alignSelf:'center' }}>vs</div>
        <div style={{ textAlign:'center' }}>
          <div style={{ fontFamily:'var(--mono)', fontSize: 28, fontWeight: 700, color: sb > sa ? 'var(--green)' : 'var(--muted)' }}>
            {sb > 0 ? '+' : ''}{sb.toFixed(1)}
          </div>
          <div style={{ fontFamily:'var(--mono)', fontSize: 10, color:'var(--muted)' }}>{nameB} SCORE</div>
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
    { label: 'TREND',     val: data.trend_score },
    { label: 'MOMENTUM',  val: data.momentum_score },
    { label: 'REGIME',    val: data.regime_score },
    { label: 'VOLATILITY',val: data.volatility_score },
    { label: 'MACRO',     val: data.macro_score },
    { label: 'SENTIMENT', val: data.sentiment_score },
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
            <div style={{ fontFamily:'var(--mono)', fontSize: 22, fontWeight: 700, color: 'var(--white)', letterSpacing: '0.06em' }}>
              {ticker}
            </div>
            <div style={{ fontFamily:'var(--mono)', fontSize: 10, color:'var(--muted)', marginTop: 2 }}>
              {data.name} · {(data.asset_class || '').toUpperCase()}
            </div>
          </div>
          <div style={{ textAlign:'right' }}>
            <div style={{ fontFamily:'var(--mono)', fontSize: 20, fontWeight: 700, color: 'var(--white)' }}>
              ${price.toLocaleString(undefined, { minimumFractionDigits:2, maximumFractionDigits:2 })}
            </div>
            <div style={{ fontFamily:'var(--mono)', fontSize: 10, color: parseFloat(pctFrom52H) < 0 ? 'var(--red)' : 'var(--green)' }}>
              {pctFrom52H}% from 52W HIGH
            </div>
          </div>
        </div>

        {/* Action + Archetype */}
        <div style={{ marginTop: 12, display:'flex', gap: 8, flexWrap:'wrap', alignItems:'center' }}>
          <div style={{
            fontFamily:'var(--mono)', fontSize:13, fontWeight:700, color: actionColor,
            background: `${actionColor}18`, border:`1px solid ${actionColor}44`,
            padding:'3px 10px', borderRadius:2,
          }}>
            {action.toUpperCase()}
          </div>
          <div style={{
            fontFamily:'var(--mono)', fontSize:10, color: color,
            background:`${color}15`, border:`1px solid ${color}33`,
            padding:'3px 10px', borderRadius:2,
          }}>
            {archetype}
          </div>
          <div style={{
            fontFamily:'var(--mono)', fontSize:10,
            color: data.confidence === 'High' ? 'var(--green)' : data.confidence === 'Low' ? 'var(--red)' : 'var(--yellow)',
          }}>
            {data.confidence?.toUpperCase()} CONVICTION
          </div>
        </div>
      </div>

      {/* Score gauge + sub-scores */}
      <div className="bb-card">
        <div style={{ display:'flex', gap:16, alignItems:'flex-start' }}>
          <div style={{ display:'flex', flexDirection:'column', alignItems:'center' }}>
            <ScoreGauge score={data.composite_score} size={130} />
            <div style={{ fontFamily:'var(--mono)', fontSize:9, color:'var(--muted)', marginTop:-4 }}>COMPOSITE SCORE</div>
          </div>
          <div style={{ flex:1 }}>
            {sub.map(s => <ScoreBar key={s.label} score={s.val} label={s.label} />)}
          </div>
        </div>
      </div>

      {/* Radar chart */}
      <div className="bb-card">
        <div className="bb-card-header" style={{ color }}>SIGNAL COMPOSITION</div>
        <ResponsiveContainer width="100%" height={160}>
          <RadarChart data={radarData} margin={{ top:4, right:16, bottom:4, left:16 }}>
            <PolarGrid stroke="#1e1e1e" />
            <PolarAngleAxis dataKey="subject" tick={{ fill:'var(--muted)', fontSize:9, fontFamily:'var(--mono)' }} />
            <Radar dataKey="value" stroke={color} fill={color} fillOpacity={0.15} strokeWidth={1.5} />
          </RadarChart>
        </ResponsiveContainer>
      </div>

      {/* Trade thesis */}
      {thesis.bull_case && (
        <div className="bb-card">
          <div className="bb-card-header" style={{ color:'var(--green)' }}>BULL CASE</div>
          {(thesis.bull_case || []).slice(0,4).map((b, i) => (
            <div key={i} style={{ display:'flex', gap:6, marginBottom:6 }}>
              <span style={{ color:'var(--green)', fontFamily:'var(--mono)', fontSize:10, flexShrink:0 }}>▲</span>
              <span style={{ fontFamily:'var(--mono)', fontSize:11, color:'var(--text)', lineHeight:1.4 }}>{b}</span>
            </div>
          ))}
        </div>
      )}

      {thesis.bear_case && (
        <div className="bb-card">
          <div className="bb-card-header" style={{ color:'var(--red)' }}>BEAR CASE / RISKS</div>
          {(thesis.bear_case || []).slice(0,4).map((b, i) => (
            <div key={i} style={{ display:'flex', gap:6, marginBottom:6 }}>
              <span style={{ color:'var(--red)', fontFamily:'var(--mono)', fontSize:10, flexShrink:0 }}>▼</span>
              <span style={{ fontFamily:'var(--mono)', fontSize:11, color:'var(--text)', lineHeight:1.4 }}>{b}</span>
            </div>
          ))}
        </div>
      )}

      {/* Invalidation */}
      {thesis.invalidation?.length > 0 && (
        <div className="bb-card" style={{ borderColor:'#332200' }}>
          <div className="bb-card-header" style={{ color:'var(--yellow)' }}>INVALIDATION TRIGGERS</div>
          {thesis.invalidation.slice(0,3).map((inv, i) => (
            <div key={i} style={{ display:'flex', gap:6, marginBottom:5 }}>
              <span style={{ color:'var(--yellow)', fontFamily:'var(--mono)', fontSize:10, flexShrink:0 }}>!</span>
              <span style={{ fontFamily:'var(--mono)', fontSize:11, color:'var(--muted)', lineHeight:1.4 }}>{inv}</span>
            </div>
          ))}
        </div>
      )}

      {/* Risk params */}
      <div className="bb-card">
        <div className="bb-card-header">RISK PARAMETERS</div>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8 }}>
          {[
            ['STOP LOSS',    `$${parseFloat(data.stop_loss||0).toFixed(2)}`,    'var(--red)'],
            ['TAKE PROFIT',  `$${parseFloat(data.take_profit||0).toFixed(2)}`,  'var(--green)'],
            ['POSITION SIZE',`${(data.position_size_pct||0).toFixed(1)}%`,      color],
            ['REALISED VOL', `${((data.realised_vol||0)*100).toFixed(1)}%`,     'var(--muted)'],
            ['ATR %',        `${((data.atr_pct||0)*100).toFixed(2)}%`,          'var(--muted)'],
            ['RISK LEVEL',   data.risk_level,
              data.risk_level === 'Very High' ? 'var(--red)' : data.risk_level === 'High' ? 'var(--yellow)' : 'var(--green)'],
          ].map(([lbl, val, c]) => (
            <div key={lbl} style={{ background:'var(--bg-3)', padding:'8px 10px', borderRadius:2 }}>
              <div style={{ fontFamily:'var(--mono)', fontSize:9, color:'var(--muted)', marginBottom:3, letterSpacing:'0.08em' }}>{lbl}</div>
              <div style={{ fontFamily:'var(--mono)', fontSize:13, fontWeight:700, color: c || 'var(--white)' }}>{val}</div>
            </div>
          ))}
        </div>
      </div>

      {/* 52W range */}
      <div className="bb-card">
        <div className="bb-card-header">52-WEEK RANGE</div>
        <div style={{ display:'flex', justifyContent:'space-between', marginBottom:6 }}>
          <span style={{ fontFamily:'var(--mono)', fontSize:11, color:'var(--red)' }}>${low52.toFixed(2)}</span>
          <span style={{ fontFamily:'var(--mono)', fontSize:11, color:'var(--white)' }}>${price.toFixed(2)}</span>
          <span style={{ fontFamily:'var(--mono)', fontSize:11, color:'var(--green)' }}>${high52.toFixed(2)}</span>
        </div>
        <div style={{ background:'#1a1a1a', borderRadius:2, height:8, position:'relative' }}>
          {high52 > low52 && (
            <div style={{
              position:'absolute', left:0, top:0, height:'100%',
              width:`${((price - low52) / (high52 - low52)) * 100}%`,
              background: `linear-gradient(to right, var(--red-dim), ${color})`,
              borderRadius:2, maxWidth:'100%',
            }} />
          )}
        </div>
      </div>

      {/* Signal conflicts */}
      {conflicts.length > 0 && (
        <div className="bb-card" style={{ borderColor: highConflicts.length > 0 ? '#330000' : '#221100' }}>
          <div className="bb-card-header" style={{ color:'var(--yellow)' }}>
            SIGNAL CONFLICTS ({conflicts.length}) {highConflicts.length > 0 && `· ${highConflicts.length} HIGH`}
          </div>
          {conflicts.slice(0,3).map((c, i) => <ConflictRow key={i} conflict={c} />)}
          {data.conflict_penalty > 0 && (
            <div style={{ fontFamily:'var(--mono)', fontSize:10, color:'var(--muted)', marginTop:6 }}>
              Score penalised by {data.conflict_penalty?.toFixed(1)} pts due to conflicts
            </div>
          )}
        </div>
      )}

      {/* Sizing rationale */}
      {thesis.sizing_rationale && (
        <div className="bb-card" style={{ borderColor:'#112233' }}>
          <div className="bb-card-header" style={{ color:'var(--blue)' }}>POSITION SIZING</div>
          <div style={{ fontFamily:'var(--mono)', fontSize:11, color:'var(--text)', lineHeight:1.5 }}>
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
      <div className="bb-card-header">HEAD-TO-HEAD COMPARISON</div>
      <table>
        <thead>
          <tr>
            <th>METRIC</th>
            <th style={{ color:'var(--orange)' }}>{nameA}</th>
            <th style={{ color:'#00aaff' }}>{nameB}</th>
            <th>EDGE</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, va, vb, numeric, lowerBetter]) => {
            let edge = '—'
            let edgeColor = 'var(--muted)'
            if (numeric && va !== undefined && vb !== undefined) {
              const na = parseFloat(va), nb = parseFloat(vb)
              if (!isNaN(na) && !isNaN(nb)) {
                const aBetter = lowerBetter ? na < nb : na > nb
                edge = aBetter ? nameA : nb !== na ? nameB : '—'
                edgeColor = aBetter ? 'var(--orange)' : nb !== na ? '#00aaff' : 'var(--muted)'
              }
            }
            return (
              <tr key={label}>
                <td style={{ color:'var(--muted)', fontSize:11 }}>{label}</td>
                <td style={{ color:'var(--orange)', fontWeight:600 }}>{va ?? '—'}</td>
                <td style={{ color:'#00aaff', fontWeight:600 }}>{vb ?? '—'}</td>
                <td style={{ color: edgeColor, fontWeight:700, fontSize:11 }}>{edge}</td>
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
          <div style={{ fontFamily:'var(--mono)', fontSize:14, fontWeight:700, color:'var(--orange)', letterSpacing:'0.1em' }}>
            ⚡ STOCK COMPARISON TERMINAL
          </div>
          <div style={{ fontFamily:'var(--mono)', fontSize:10, color:'var(--muted)', marginTop:2 }}>
            {time.toLocaleTimeString('en-US', { hour12:false })} · ARIA SIGNAL ENGINE · SITUATION-AWARE ANALYSIS
          </div>
        </div>

        {/* Ticker inputs */}
        <div style={{ display:'flex', gap:8, alignItems:'center' }}>
          <input
            value={inputA}
            onChange={e => setInputA(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && handleCompare()}
            style={{
              background:'var(--bg-2)', border:'1px solid var(--orange)', borderRadius:2,
              padding:'7px 10px', color:'var(--orange)', fontFamily:'var(--mono)',
              fontSize:14, fontWeight:700, width:90, textAlign:'center', letterSpacing:'0.1em',
            }}
          />
          <span style={{ fontFamily:'var(--mono)', color:'var(--muted)', fontSize:12 }}>vs</span>
          <input
            value={inputB}
            onChange={e => setInputB(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && handleCompare()}
            style={{
              background:'var(--bg-2)', border:'1px solid #00aaff', borderRadius:2,
              padding:'7px 10px', color:'#00aaff', fontFamily:'var(--mono)',
              fontSize:14, fontWeight:700, width:90, textAlign:'center', letterSpacing:'0.1em',
            }}
          />
          <button
            onClick={handleCompare}
            style={{
              background:'var(--orange)', color:'#000', border:'none', borderRadius:2,
              padding:'7px 16px', fontFamily:'var(--mono)', fontSize:11, fontWeight:700,
              cursor:'pointer', letterSpacing:'0.08em',
            }}>
            ANALYZE
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
            fontFamily:'var(--mono)', fontSize:11, fontWeight:700, color:'var(--orange)',
            letterSpacing:'0.12em', textAlign:'center', marginBottom:8,
            padding:'5px', background:'var(--orange-bg)', border:'1px solid var(--border)',
          }}>
            ── {tickerA} ──
          </div>
          {errA && <div style={{ fontFamily:'var(--mono)', fontSize:11, color:'var(--red)', marginBottom:8 }}>⚠ {errA}</div>}
          {!loadingA && (
            <SignalColumn ticker={tickerA} data={dataA} color="var(--orange)" />
          )}
        </div>

        {/* Divider */}
        <div style={{ width:1, background:'var(--border-2)', alignSelf:'stretch', flexShrink:0 }} />

        {/* Column B */}
        <div style={{ flex:1, minWidth:0 }}>
          <div style={{
            fontFamily:'var(--mono)', fontSize:11, fontWeight:700, color:'#00aaff',
            letterSpacing:'0.12em', textAlign:'center', marginBottom:8,
            padding:'5px', background:'rgba(0,170,255,0.06)', border:'1px solid var(--border)',
          }}>
            ── {tickerB} ──
          </div>
          {errB && <div style={{ fontFamily:'var(--mono)', fontSize:11, color:'var(--red)', marginBottom:8 }}>⚠ {errB}</div>}
          {!loadingB && (
            <SignalColumn ticker={tickerB} data={dataB} color="#00aaff" />
          )}
        </div>
      </div>

      {/* Disclaimer */}
      <div style={{ marginTop:24, fontFamily:'var(--mono)', fontSize:9, color:'#333', textAlign:'center', lineHeight:1.6 }}>
        ⚠ ARIA IS A RESEARCH TOOL. ALL SIGNALS ARE PROBABILISTIC AND NOT FINANCIAL ADVICE.
        PAST SIGNAL ACCURACY DOES NOT GUARANTEE FUTURE RESULTS. TRADE AT YOUR OWN RISK.
      </div>
    </div>
  )
}
