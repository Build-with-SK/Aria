import React, { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { technicalEvidence } from './technicalEvidence'

const colors = { 'Strong Buy': 'var(--green)', Buy: 'var(--green)', Sell: 'var(--red)', 'Strong Sell': 'var(--red)' }
const text = { fontFamily: 'var(--mono)', fontSize: 11, lineHeight: 1.6 }
const frames = ['1m', '5m', '15m', '30m', '1h', '5h', '1d', '1w', '1M']
const cell = { padding: '6px 8px', borderBottom: '1px solid var(--border)', textAlign: 'left' }

export default function TechnicalDashboard({ symbol, tech, onRefresh }) {
  const [selected, setSelected] = useState('1d')
  const [review, setReview] = useState(null)
  const [busy, setBusy] = useState(false)
  const generation = useRef(0)
  useEffect(() => {
    generation.current += 1; setReview(null); setBusy(false)
    return () => { generation.current += 1 }
  }, [symbol, tech])
  if (!tech) return <div style={text}>Technical evidence loads when analysis runs.</div>
  if (tech.error) return <div role="alert" style={text}>{tech.error}</div>
  const rows = tech.timeframes || {}
  const withheld = Object.entries(rows).filter(([, row]) => !row.summary || row.data_stale || row.error).map(([tf]) => tf)
  const detail = rows[selected]
  const conflicts = tech.conflicts || []
  const disagreements = review?.sentinel_position === 'DISAGREE' || review?.sentinel_position === 'WARNING' || (review?.counter_arguments || []).length > 0

  async function ask() {
    const current = ++generation.current
    setBusy(true); setReview(null)
    // Deliberately supply evidence without an overall directional hypothesis.
    // The authenticated owner endpoint uses the existing scrubbed consultation bridge.
    const context = technicalEvidence(symbol, rows)
    if (encodeURIComponent(context).length > 14000) {
      setReview({ ok: false, sentinel_status: 'EVIDENCE_TOO_LARGE', guidance: 'Evidence exceeds the current consultation interface limit; use the System consultation panel for a focused timeframe review.' })
      setBusy(false); return
    }
    try {
      const response = await axios.post('/api/consult', null, { params: {
        question: `Independently assess the supplied technical evidence for ${symbol}. Identify conflicting horizons, market structure, risk and missing data. Do not call ARIA, place orders, invent prices, or use unrelated web pages as evidence about this instrument. Distinguish interpretation of supplied candles from independently verified market data. Return counter_arguments and uncertainties explicitly.`,
        context,
      }, timeout: 150000 })
      if (generation.current === current) setReview(response.data)
    } catch (error) {
      if (generation.current === current) setReview({ ok: false, sentinel_status: error?.response?.status === 403 ? 'OWNER_ACCESS_REQUIRED' : 'UNAVAILABLE', guidance: 'No independent review obtained. Silence is not agreement.' })
    } finally { if (generation.current === current) setBusy(false) }
  }

  return <section aria-label="Multi-timeframe technical recommendations" style={{ ...text, marginTop: 16 }}>
    <div style={{ display: 'flex', gap: 12, alignItems: 'baseline', flexWrap: 'wrap' }}>
      <strong style={{ color: 'var(--orange)' }}>MULTI-TIMEFRAME TECHNICAL EVIDENCE</strong>
      {onRefresh && <button onClick={onRefresh} style={{ font: 'inherit', background: 'none', color: 'var(--orange)', border: '1px solid var(--border)', padding: 5, cursor: 'pointer' }}>REFRESH ANALYSIS</button>}
    </div>
    <p>{tech.assessment || 'Legacy response — detailed evidence requires the updated backend module.'}</p>
    {conflicts.length > 0 && <div role="alert" style={{ border: '1px solid var(--orange)', padding: 10, marginBottom: 10 }}>
      {conflicts.map((c, i) => <div key={i}>{c.warning}</div>)}
    </div>}
    <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1080 }}>
      <thead><tr>{['FRAME', 'RECOMMENDATION', 'INDICATORS', 'MOVING AVERAGES / TREND', 'MOMENTUM', 'VOLATILITY', 'FRESHNESS', 'EVIDENCE'].map(h => <th key={h} style={cell}>{h}</th>)}</tr></thead>
      <tbody>{frames.map(tf => {
        const d = rows[tf] || (tf === '1w' ? rows['1wk'] : null)
        return <tr key={tf} style={{ background: selected === tf ? 'rgba(255,150,0,.07)' : 'transparent' }}>
          <td style={cell}><button aria-pressed={selected === tf} onClick={() => setSelected(tf)} style={{ font: 'inherit', color: 'var(--orange)', background: 'none', border: '1px solid var(--border)', cursor: 'pointer' }}>{tf}</button></td>
          <td style={{ ...cell, color: colors[d?.summary] || 'var(--muted)' }}>{d?.summary || 'Unavailable'}{d?.error && <div>{d.error}</div>}</td>
          <td style={cell}>{d?.oscillators?.label || '—'}</td>
          <td style={cell}>{d?.moving_averages?.label || '—'}</td>
          <td style={cell}>{d?.momentum || '—'}</td>
          <td style={cell}>{d?.volatility ? `${d.volatility.regime}; ATR ${Number(d.volatility.atr14).toFixed(3)}` : '—'}</td>
          <td style={cell}>{d?.data_as_of || 'Unverified'}<div>{d?.data_stale ? `STALE${d.market_session ? ` · session ${d.market_session}` : ''}` : d?.data_as_of ? `Closed ${d.last_closed_at || 'unverified'}` : ''}</div></td>
          <td style={cell}>{d?.evidence?.quality || d?.data_quality || 'Unavailable'}{d?.evidence && <div>{d.evidence.bars} bars · {d.evidence.ma_votes}/8 MA · {d.evidence.indicator_votes}/9 indicators</div>}{d?.verification && <div>Identity: {d.verification.identity} · history: {d.verification.history}</div>}</td>
        </tr>
      })}</tbody>
    </table></div>
    {detail && !detail.error && <div style={{ marginTop: 12 }}>
      <div>OBSERVED DATA · {detail.instrument?.provider_symbol} · exchange {detail.provenance?.exchange || detail.instrument?.exchange || 'unverified'} · close {detail.price} · {detail.provenance?.provider}</div>
      <div>DATA LIMITATIONS · {detail.provenance?.alignment || detail.latency} · {detail.gap_count} gaps · {detail.calendar_status || 'calendar unverified'} · {detail.open_bars_excluded} open bars excluded</div>
      {detail.verification && <div>FEED CHECKS · identity {detail.verification.identity} · OHLC {detail.verification.ohlc} · {detail.verification.history} · independent price validation: {detail.verification.independent_price_validation}</div>}
      {!!detail.history_prefix_excluded && <div style={{ color: 'var(--orange)' }}>Indicators restarted after the last invalid candle: {detail.history_prefix_excluded} earlier rows excluded; {detail.evidence?.bars} consecutive valid bars used. No prices were repaired.</div>}
      <div>CALCULATED INDICATORS · {selected} · {detail.confirmation?.structure} · support {detail.confirmation?.support20} · resistance {detail.confirmation?.resistance20}</div>
      {detail.calculated_summary && <div style={{ color: 'var(--orange)' }}>Historical/provisional calculation: {detail.calculated_summary}. Current recommendation withheld.</div>}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 20, marginTop: 8 }}>{[['Indicators', detail.indicator_values], ['Moving averages', detail.moving_average_values]].map(([label, values]) => <div key={label} style={{ flex: '1 1 280px' }}>
        <strong>{label}</strong><table style={{ width: '100%', borderCollapse: 'collapse' }}><thead><tr><th style={cell}>NAME</th><th style={cell}>VALUE</th><th style={cell}>VOTE</th></tr></thead>
          <tbody>{Object.entries(values || {}).map(([key, value]) => <tr key={key}><td style={cell}>{key}</td><td style={cell}>{Number(value.value).toFixed(3)}</td><td style={cell}>{value.signal}</td></tr>)}</tbody></table>
      </div>)}</div>
    </div>}
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginTop: 16 }}>
      <div style={{ flex: '1 1 300px', border: '1px solid var(--border)', padding: 10 }}><strong>ARIA · CALCULATED ASSESSMENT</strong><p>{tech.assessment}</p>Consensus: {tech.consensus || 'Withheld'}<div>Descriptive evidence; no calibrated win probability or validated trade setup.</div></div>
      <div style={{ flex: '1 1 300px', border: `1px solid ${disagreements ? 'var(--orange)' : 'var(--border)'}`, padding: 10 }}>
        <strong>SENTINEL · ADVISORY MODEL INTERPRETATION</strong>
        <div>Separate reasoning over supplied evidence. Independent market-data validation is unverified.</div>
        <button disabled={busy || !Object.values(rows).some(d => d.indicator_values)} onClick={ask} style={{ marginTop: 8, font: 'inherit', background: 'none', color: 'var(--orange)', border: '1px solid var(--orange)', cursor: 'pointer', padding: 6 }}>{busy ? 'Review in progress…' : 'REQUEST INDEPENDENT REVIEW'}</button>
        <p>{review ? `${review.sentinel_status || 'UNVERIFIED'} · ${review.sentinel_position || 'NONE'}` : 'NOT CONSULTED · No independent review obtained. Silence is not agreement.'}</p>
        {review?.ok && withheld.length > 0 && <div role="alert" style={{ color: 'var(--orange)' }}>EVIDENCE BOUNDARY · ARIA withholds current recommendations for {withheld.join(', ')}. Any directional claims by the reviewer on these horizons remain unsupported.</div>}
        {disagreements && <div role="alert" style={{ color: 'var(--orange)' }}>SENTINEL raises objections. Conclusions remain separate.</div>}
        {review && <div style={{ whiteSpace: 'pre-wrap' }}>{review.ok ? String(review.independent_analysis || review.conclusion || review.summary || 'No usable interpretation returned.') : review.guidance}</div>}
        {['counter_arguments', 'warnings', 'uncertainties'].map(key => (Array.isArray(review?.[key]) ? review[key] : []).map((point, i) => <div key={`${key}-${i}`}>{key}: {typeof point === 'string' ? point : JSON.stringify(point)}</div>))}
      </div>
    </div>
  </section>
}
