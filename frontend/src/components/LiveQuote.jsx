/**
 * components/LiveQuote.jsx
 * The price that actually moves.
 *
 * The old header read /api/universe/quote — a 15-minute-cached DAILY CLOSE, so
 * it could not change during a session however long you watched it. This polls
 * the live endpoint and, just as importantly, says WHY the price is still when
 * it is still: a closed market and a broken feed look identical otherwise.
 *
 * It does not claim zero latency. Yahoo is real-time for most US listings and
 * commonly ~15 minutes delayed elsewhere, so the tick timestamp is always on
 * screen and delayed venues carry a note.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { useCurrency } from '../currency/CurrencyContext'

const MONO = 'var(--mono)'
const POLL_OPEN_MS = 10000       // while the market is trading
const POLL_CLOSED_MS = 60000     // when it is shut, back right off

const STATE_STYLE = {
  OPEN: { color: 'var(--green)', label: 'LIVE' },
  CLOSED: { color: 'var(--muted)', label: 'MARKET CLOSED' },
  UNKNOWN: { color: 'var(--yellow)', label: 'STATE UNKNOWN' },
}

/* intraday shape — a sparkline, not a chart; the real chart sits below */
function Spark({ points, up }) {
  if (!points || points.length < 3) return null
  const w = 132, h = 34
  const lo = Math.min(...points), hi = Math.max(...points)
  const span = hi - lo || 1
  const d = points.map((v, i) =>
    `${i === 0 ? 'M' : 'L'} ${(i / (points.length - 1)) * w} ${h - 2 - ((v - lo) / span) * (h - 4)}`
  ).join(' ')
  const col = up ? 'var(--green)' : 'var(--red)'
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      <path d={d} fill="none" stroke={col} strokeWidth="1.4" />
    </svg>
  )
}

export default function LiveQuote({ symbol, nativeCurrency }) {
  const { price: toLocal } = useCurrency()
  const [q, setQ] = useState(null)
  const [err, setErr] = useState('')
  const [pulse, setPulse] = useState(0)
  const lastPrice = useRef(null)
  const timer = useRef(null)

  const load = useCallback(() => {
    if (!symbol) return
    axios.get(`/api/quote/live/${encodeURIComponent(symbol)}`)
      .then(r => {
        const d = r.data
        // Flash only on an actual change, so the pulse means something.
        if (lastPrice.current != null && d.price !== lastPrice.current) {
          setPulse(d.price > lastPrice.current ? 1 : -1)
          setTimeout(() => setPulse(0), 900)
        }
        lastPrice.current = d.price
        setQ(d); setErr('')
      })
      .catch(e => setErr(e?.response?.data?.detail || 'live quote unavailable'))
  }, [symbol])

  useEffect(() => {
    lastPrice.current = null
    setQ(null)
    load()
    const schedule = () => {
      const ms = q?.market_state === 'OPEN' ? POLL_OPEN_MS : POLL_CLOSED_MS
      timer.current = setTimeout(() => { load(); schedule() }, ms)
    }
    schedule()
    return () => clearTimeout(timer.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, load])

  if (err && !q) return <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--muted)' }}>{err}</span>
  if (!q) return <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--muted)' }}>loading live price…</span>

  const up = (q.change_pct ?? 0) >= 0
  const st = STATE_STYLE[q.market_state] || STATE_STYLE.UNKNOWN
  const ccy = q.currency || nativeCurrency
  const shown = toLocal(q.price, { from: ccy })
  const asOf = q.as_of ? new Date(q.as_of) : null

  const cell = (label, value) => (
    <div>
      <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--muted)', letterSpacing: '.14em' }}>{label}</div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: 'var(--text)' }}>{value}</div>
    </div>
  )

  return (
    <div style={{ display: 'flex', gap: 20, alignItems: 'center', flexWrap: 'wrap' }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <span title={shown.title} style={{
            fontFamily: MONO, fontSize: 26, fontWeight: 800, color: '#fff',
            transition: 'color .25s',
            ...(pulse ? { color: pulse > 0 ? 'var(--green)' : 'var(--red)' } : {}),
          }}>{shown.text}</span>
          <span style={{ fontFamily: MONO, fontSize: 14, fontWeight: 700, color: up ? 'var(--green)' : 'var(--red)' }}>
            {up ? '+' : ''}{q.change_pct == null ? '—' : q.change_pct.toFixed(2)}%
          </span>
          {shown.converted && (
            <span style={{ fontFamily: MONO, fontSize: 10, color: 'var(--muted)' }}>
              native {ccy === 'GBp' ? `${q.price}p` : q.price} {ccy}
            </span>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginTop: 3 }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%', background: st.color,
            boxShadow: q.market_state === 'OPEN' ? `0 0 8px ${st.color}` : 'none',
            animation: q.market_state === 'OPEN' ? 'corePulse 2s ease-in-out infinite' : 'none',
          }} />
          <span style={{ fontFamily: MONO, fontSize: 9, color: st.color, letterSpacing: '.12em' }}>{st.label}</span>
          {asOf && (
            <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--muted)' }}>
              · last tick {asOf.toLocaleString(undefined, {
                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
              })}
              {q.market_state === 'OPEN' ? ' · updating every 10s' : ''}
            </span>
          )}
        </div>
        {q.delayed_hint && (
          <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--yellow)', marginTop: 2, maxWidth: 430, lineHeight: 1.5 }}>
            ⚑ {q.delayed_hint}
          </div>
        )}
      </div>

      <Spark points={q.intraday} up={up} />

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        {cell('PREV CLOSE', toLocal(q.previous_close, { from: ccy }).text)}
        {cell('OPEN', toLocal(q.open, { from: ccy }).text)}
        {cell('DAY RANGE', `${toLocal(q.day_low, { from: ccy }).text} – ${toLocal(q.day_high, { from: ccy }).text}`)}
        {cell('VOLUME', q.volume ? Number(q.volume).toLocaleString() : '—')}
      </div>
    </div>
  )
}
