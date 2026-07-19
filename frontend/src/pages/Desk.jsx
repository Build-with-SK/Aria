import React, { useEffect, useRef, useState } from 'react'

/*
  Desk.jsx — THE AUTONOMOUS TRADING DESK (ARIA v3)
  Debate theatre · trade slate · AUTO-EXECUTE toggle (paper-only, hard-gated)
  Fills feed · paper P&L. Crimson command-deck aesthetic, inline styles only.
*/

const MONO = 'var(--mono)'

function usePoll(url, ms) {
  const [data, setData] = useState(null)
  useEffect(() => {
    let dead = false
    const load = () => fetch(url).then(r => r.json()).then(d => { if (!dead) setData(d) }).catch(() => {})
    load()
    const id = setInterval(load, ms)
    return () => { dead = true; clearInterval(id) }
  }, [url, ms])
  return data
}

/* ── typewriter for debate arguments (throttled — ~25 fps, chunked) ── */
function Typer({ text, speed = 40, instant }) {
  const [n, setN] = useState(instant ? (text || '').length : 0)
  useEffect(() => {
    if (instant || !text || text.length > 1200) { setN((text || '').length); return }
    setN(0)
    const id = setInterval(() => {
      setN(v => {
        if (v >= text.length) { clearInterval(id); return v }
        return v + 14
      })
    }, speed)
    return () => clearInterval(id)
  }, [text, instant, speed])
  return <span>{(text || '').slice(0, n)}{n < (text || '').length && <span className="blink">▍</span>}</span>
}

/* ── conviction gauge — semicircular SVG, needle lands on the verdict ── */
function ConvictionGauge({ conviction = 0, bar = 65, verdict = 'NO_TRADE' }) {
  const angle = -90 + (Math.min(conviction, 100) / 100) * 180
  const barAngle = -90 + (Math.min(bar, 100) / 100) * 180
  const color = verdict === 'BUY' ? 'var(--green)' : verdict === 'SELL' ? 'var(--red)' : 'var(--yellow)'
  const arc = (a0, a1, r, w, stroke, opacity = 1) => {
    const p = a => [90 + r * Math.cos((a - 90) * Math.PI / 180), 90 + r * Math.sin((a - 90) * Math.PI / 180)]
    const [x0, y0] = p(a0), [x1, y1] = p(a1)
    return <path d={`M ${x0} ${y0} A ${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1} ${y1}`}
      fill="none" stroke={stroke} strokeWidth={w} opacity={opacity} strokeLinecap="round" />
  }
  return (
    <svg viewBox="0 0 180 100" style={{ width: '100%', maxWidth: 220 }} className="gauge-svg">
      {arc(-90, 90, 70, 9, '#1c0c12')}
      {arc(-90, angle, 70, 9, color, 0.9)}
      {/* the bar the judge must clear */}
      <line x1={90 + 58 * Math.sin(barAngle * Math.PI / 180)} y1={90 - 58 * Math.cos(barAngle * Math.PI / 180)}
        x2={90 + 80 * Math.sin(barAngle * Math.PI / 180)} y2={90 - 80 * Math.cos(barAngle * Math.PI / 180)}
        stroke="var(--orange)" strokeWidth="2" strokeDasharray="3 2" />
      <line x1="90" y1="90" x2={90 + 52 * Math.sin(angle * Math.PI / 180)} y2={90 - 52 * Math.cos(angle * Math.PI / 180)}
        stroke={color} strokeWidth="2.5" style={{ transition: 'all .8s cubic-bezier(.2,.8,.25,1)' }} />
      <circle cx="90" cy="90" r="5" fill={color} />
      <text x="90" y="72" textAnchor="middle" fontSize="20" fontWeight="800" fill={color}>{conviction}</text>
      <text x="90" y="84" textAnchor="middle" fontSize="7" fill="var(--muted)">BAR {bar}</text>
    </svg>
  )
}

/* ── AUTO-EXECUTE toggle — the guard is always visible ── */
function AutoExecToggle({ status, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState('')
  const gates = status?.gates || {}
  const armed = !!status?.config?.auto_execute
  const account = gates.account || 'disconnected'
  const paperOk = gates.env_paper && account === 'paper'
  const live = account === 'live'

  const toggle = async () => {
    setBusy(true); setRefusal('')
    try {
      const r = await fetch(`/api/desk/auto-execute?enabled=${!armed}`, { method: 'POST' })
      if (!r.ok) {
        const e = await r.json().catch(() => ({}))
        setRefusal(e.detail || `Refused (${r.status})`)
      }
      onChanged && onChanged()
    } finally { setBusy(false) }
  }

  return (
    <div className="bb-card glow-frame" style={{ padding: 16 }}>
      <div className="bb-card-header">AUTONOMOUS EXECUTION</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <button onClick={toggle} disabled={busy || live} style={{
          fontFamily: MONO, fontSize: 13, fontWeight: 800, letterSpacing: '.14em',
          padding: '14px 26px', borderRadius: 6, cursor: live ? 'not-allowed' : 'pointer',
          background: armed ? 'var(--green-bg)' : 'var(--bg-3)',
          color: armed ? 'var(--green)' : live ? 'var(--muted)' : 'var(--text-dim)',
          border: `1px solid ${armed ? 'var(--green-dim)' : 'var(--border-2)'}`,
          boxShadow: armed ? '0 0 18px rgba(43,227,139,.3)' : 'none',
          animation: armed ? 'pulseGlow 3.2s ease-in-out infinite' : 'none',
        }}>
          {armed ? '■ AUTO-EXECUTE ARMED' : '▶ ARM AUTO-EXECUTE'}
        </button>
        <div style={{ fontFamily: MONO, fontSize: 11, lineHeight: 1.8 }}>
          <div style={{ color: paperOk ? 'var(--green)' : live ? 'var(--red)' : 'var(--yellow)', fontWeight: 700 }}>
            {account === 'paper' ? '● PAPER — auto-exec permitted' :
              live ? '○ LIVE — manual only (locked)' : '○ BROKER DISCONNECTED — manual only'}
          </div>
          <div style={{ color: 'var(--muted)', fontSize: 10 }}>
            ALPACA_PAPER gate: <span style={{ color: gates.env_paper ? 'var(--green)' : 'var(--red)' }}>
              {gates.env_paper ? 'PASS' : 'FAIL'}</span>
            {' · '}budget left <span className="white">${(gates.budget_left ?? 0).toLocaleString()}</span>
            {' · '}trades left <span className="white">{gates.trades_left ?? '—'}</span>
          </div>
          <div style={{ color: 'var(--muted)', fontSize: 10 }}>
            {armed ? 'Fills happen on their own — you are informed after, never asked.' :
              'OFF by default. Trades wait in the approval queue until you arm it.'}
          </div>
        </div>
      </div>
      {refusal && (
        <div style={{
          marginTop: 10, padding: '8px 12px', borderRadius: 4, fontFamily: MONO, fontSize: 11,
          background: 'var(--red-bg)', color: 'var(--red)', border: '1px solid var(--red-dim)',
        }}>⛔ {refusal}</div>
      )}
    </div>
  )
}

/* ── debate theatre ── */
function DebateTheatre({ debates }) {
  const [sel, setSel] = useState(0)
  const list = debates?.debates || []
  const d = list[sel]
  const seenRef = useRef(new Set())
  const isNew = d && !seenRef.current.has(d.id)
  useEffect(() => { if (d) { const t = setTimeout(() => seenRef.current.add(d.id), 4000); return () => clearTimeout(t) } }, [d])

  if (!list.length) return (
    <div className="bb-card"><div className="bb-card-header">DEBATE THEATRE</div>
      <div style={{ color: 'var(--muted)', fontFamily: MONO, fontSize: 11, padding: 20 }}>
        No debates yet — hit RUN CYCLE and the researchers will take the floor.
      </div></div>
  )

  const judge = d?.judge || {}
  const vColor = judge.verdict === 'BUY' ? 'var(--green)' : judge.verdict === 'SELL' ? 'var(--red)' : 'var(--yellow)'

  return (
    <div className="bb-card" style={{ padding: 14 }}>
      <div className="bb-card-header">DEBATE THEATRE — BULL vs BEAR</div>

      {/* debate selector tabs */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
        {list.map((t, i) => (
          <button key={t.id} onClick={() => setSel(i)} style={{
            fontFamily: MONO, fontSize: 10, fontWeight: 700, padding: '4px 10px',
            borderRadius: 3, cursor: 'pointer',
            background: i === sel ? 'var(--orange-bg)' : 'var(--bg-3)',
            color: i === sel ? 'var(--orange)' : 'var(--text-dim)',
            border: `1px solid ${i === sel ? 'var(--orange-dim)' : 'var(--border)'}`,
          }}>
            {t.ticker} <span style={{
              color: t.judge?.verdict === 'BUY' ? 'var(--green)' :
                t.judge?.verdict === 'SELL' ? 'var(--red)' : 'var(--yellow)'
            }}>{t.judge?.verdict}</span>
          </button>
        ))}
      </div>

      {/* two columns, round by round */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <div style={{ fontFamily: MONO, fontSize: 10, fontWeight: 800, color: 'var(--green)', letterSpacing: '.15em' }}>
          ▲ BULL RESEARCHER</div>
        <div style={{ fontFamily: MONO, fontSize: 10, fontWeight: 800, color: 'var(--red)', letterSpacing: '.15em', textAlign: 'right' }}>
          BEAR RESEARCHER ▼</div>
        {(d.rounds || []).filter(r => r.bull || r.bear).map((r, i) => (
          <React.Fragment key={i}>
            <div style={{
              background: 'var(--green-bg)', border: '1px solid rgba(43,227,139,.2)',
              borderRadius: 6, padding: '10px 12px', fontSize: 12, lineHeight: 1.55,
            }}>
              <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--green-dim)', marginBottom: 4 }}>ROUND {r.round}</div>
              <Typer text={r.bull} instant={!isNew} />
            </div>
            <div style={{
              background: 'var(--red-bg)', border: '1px solid rgba(255,85,96,.2)',
              borderRadius: 6, padding: '10px 12px', fontSize: 12, lineHeight: 1.55,
            }}>
              <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--red-dim)', marginBottom: 4, textAlign: 'right' }}>ROUND {r.round}</div>
              <Typer text={r.bear} instant={!isNew} speed={50} />
            </div>
          </React.Fragment>
        ))}
      </div>

      {(d.contradictions || []).length > 0 && (
        <div style={{
          marginTop: 10, padding: '8px 12px', borderRadius: 4, fontFamily: MONO, fontSize: 10,
          background: 'var(--yellow-bg)', color: 'var(--yellow)', border: '1px solid rgba(255,179,36,.25)',
        }}>
          ⚠ CONTRADICTION GUARD: {d.contradictions.map(c => c.flag).join(' · ')}
        </div>
      )}

      {/* judge verdict + gauge */}
      <div style={{
        marginTop: 12, display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap',
        background: 'var(--bg-3)', border: `1px solid ${vColor === 'var(--yellow)' ? 'var(--border-2)' : vColor}`,
        borderRadius: 6, padding: '12px 16px',
      }}>
        <ConvictionGauge conviction={judge.conviction} bar={judge.conviction_bar} verdict={judge.verdict} />
        <div style={{ flex: 1, minWidth: 240 }}>
          <div style={{ fontFamily: MONO, fontSize: 16, fontWeight: 800, color: vColor, letterSpacing: '.1em' }}>
            ⚖ {judge.verdict} — {d.ticker}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text)', margin: '6px 0', lineHeight: 1.5 }}>
            <Typer text={judge.reasoning} instant={!isNew} />
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--text-dim)', lineHeight: 1.7 }}>
            KEY RISK: <span className="bear">{judge.key_risk}</span><br />
            INVALIDATION: <span className="white">${judge.invalidation_level}</span>
            {' · '}REGIME: <span className="acc">{judge.regime}</span>
            {' · '}SIZE MULT: <span className="white">{judge.risk_multiplier}x</span>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ── the slate ── */
function Slate({ slate }) {
  const rows = slate?.slate || []
  const rejected = slate?.rejected || []
  return (
    <div className="bb-card">
      <div className="bb-card-header">THE SLATE — RANKED &amp; RISK-CAPPED</div>
      {!rows.length && (
        <div style={{ color: 'var(--muted)', fontFamily: MONO, fontSize: 11, padding: 12 }}>
          Slate is empty — no debate cleared the conviction bar and the risk officer this cycle.
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))', gap: 10 }}>
        {rows.map((s, i) => (
          <div key={i} style={{
            background: 'var(--bg-3)', borderRadius: 6, padding: '10px 12px',
            border: `1px solid ${s.side === 'buy' ? 'rgba(43,227,139,.3)' : 'rgba(255,85,96,.3)'}`,
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ fontFamily: MONO, fontSize: 14, fontWeight: 800, color: '#fff' }}>{s.ticker}</span>
              <span className={s.side === 'buy' ? 'pill-bull score-pill' : 'pill-bear score-pill'}>
                {s.side.toUpperCase()} {s.qty}
              </span>
            </div>
            <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--text-dim)', margin: '6px 0' }}>
              conviction <span className="white">{s.conviction}</span>/100
              {' · '}${(s.notional || 0).toLocaleString()}
              {' · '}{s.sector}
            </div>
            <div className="bar-track" style={{ marginBottom: 6 }}>
              <div className={s.side === 'buy' ? 'bar-fill-bull' : 'bar-fill-bear'} style={{ width: `${s.conviction}%` }} />
            </div>
            <div style={{ fontSize: 11, color: 'var(--text)', lineHeight: 1.45, maxHeight: 58, overflow: 'hidden' }}>
              {(s.thesis || '').slice(0, 150)}
            </div>
            <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--muted)', marginTop: 6 }}>
              stop ${s.stop?.toFixed?.(2)} · target ${s.target?.toFixed?.(2)} · debate {s.debate_id}
            </div>
          </div>
        ))}
      </div>
      {rejected.length > 0 && (
        <div style={{ marginTop: 10, fontFamily: MONO, fontSize: 10, color: 'var(--muted)', lineHeight: 1.7 }}>
          RISK OFFICER REJECTED: {rejected.map((r, i) =>
            <span key={i}><span className="bear">{r.ticker}</span> ({r.reason}){i < rejected.length - 1 ? ' · ' : ''}</span>)}
        </div>
      )}
    </div>
  )
}

/* ── fills feed ── */
function FillsFeed({ execs }) {
  const rows = execs?.executions || []
  const modeColor = m => m === 'auto' ? 'var(--green)' : m === 'queued' ? 'var(--yellow)' : 'var(--red)'
  const modeLabel = m => m === 'auto' ? 'AUTO-FILLED' : m === 'queued' ? 'QUEUED FOR APPROVAL' : m.toUpperCase()
  return (
    <div className="bb-card" style={{ maxHeight: 340, overflowY: 'auto' }}>
      <div className="bb-card-header">FILLS FEED — EVERY ACTION, EXPLAINED</div>
      {!rows.length && <div style={{ color: 'var(--muted)', fontFamily: MONO, fontSize: 11, padding: 10 }}>
        No desk actions yet.</div>}
      {rows.map((r, i) => (
        <div key={i} style={{ borderBottom: '1px solid var(--border)', padding: '8px 2px' }}>
          <div style={{ fontFamily: MONO, fontSize: 11 }}>
            <span style={{ color: r.side === 'buy' ? 'var(--green)' : 'var(--red)', fontWeight: 800 }}>
              {r.side === 'buy' ? '▲' : '▼'} {r.side?.toUpperCase()} {r.qty} {r.ticker}
            </span>
            {r.fill_price ? <span className="white"> @ ${Number(r.fill_price).toFixed(2)}</span> : null}
            <span style={{ color: modeColor(r.mode), marginLeft: 8, fontSize: 9, fontWeight: 700 }}>
              [{modeLabel(r.mode || '?')}]</span>
          </div>
          <div style={{ fontSize: 10, color: 'var(--text-dim)', margin: '3px 0', lineHeight: 1.4 }}>
            {r.mode === 'queued' ? `⏸ ${r.reason} · ` : ''}thesis: {(r.thesis || '').slice(0, 120)}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--muted)' }}>
            {new Date(r.at).toLocaleString()} · conviction {r.conviction}/100 · debate {r.debate_id}
          </div>
        </div>
      ))}
    </div>
  )
}

/* ── paper P&L ── */
function PaperPnL({ pnl }) {
  if (!pnl) return null
  const curve = pnl.equity_curve || []
  const w = 260, h = 60
  let path = null
  if (curve.length > 1) {
    const vals = curve.map(c => c.equity)
    const lo = Math.min(...vals), hi = Math.max(...vals)
    const span = hi - lo || 1
    path = vals.map((v, i) =>
      `${i === 0 ? 'M' : 'L'} ${(i / (vals.length - 1)) * w} ${h - ((v - lo) / span) * (h - 6) - 3}`).join(' ')
  }
  const pc = v => (v > 0 ? 'bull' : v < 0 ? 'bear' : 'neut')
  return (
    <div className="bb-card">
      <div className="bb-card-header">PAPER P&amp;L {pnl.account !== 'paper' && `— ${(pnl.account || '').toUpperCase()}`}</div>
      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'center' }}>
        <div>
          <div style={{ fontFamily: MONO, fontSize: 22, fontWeight: 800, color: '#fff' }}>
            ${(pnl.equity || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--muted)' }}>PAPER EQUITY</div>
        </div>
        <div style={{ fontFamily: MONO, fontSize: 11, lineHeight: 1.8 }}>
          <div>DAY <span className={pc(pnl.day_pnl)}>{pnl.day_pnl >= 0 ? '+' : ''}{(pnl.day_pnl || 0).toFixed(2)} ({(pnl.day_pnl_pct || 0).toFixed(2)}%)</span></div>
          <div>TOTAL <span className={pc(pnl.total_pnl)}>{pnl.total_pnl >= 0 ? '+' : ''}{(pnl.total_pnl || 0).toFixed(2)} ({(pnl.total_pnl_pct || 0).toFixed(2)}%)</span></div>
          <div>OPEN WIN RATE <span className="white">{pnl.open_win_rate == null ? '—' : `${Math.round(pnl.open_win_rate * 100)}%`}</span></div>
        </div>
        {path && (
          <svg viewBox={`0 0 ${w} ${h}`} style={{ width: w, height: h }}>
            <path d={path} fill="none" stroke="var(--orange)" strokeWidth="1.5"
              style={{ filter: 'drop-shadow(0 0 4px rgba(255,36,71,.5))' }} />
          </svg>
        )}
      </div>
      {(pnl.open_positions || []).length > 0 && (
        <table style={{ marginTop: 10 }}>
          <thead><tr><th>TICKER</th><th>QTY</th><th>AVG COST</th><th>VALUE</th><th>UNREAL P&amp;L</th></tr></thead>
          <tbody>
            {pnl.open_positions.map((p, i) => (
              <tr key={i}>
                <td className="white">{p.ticker}</td>
                <td>{p.qty}</td>
                <td>${Number(p.avg_cost).toFixed(2)}</td>
                <td>${Number(p.market_value).toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                <td className={p.unrealized_pl >= 0 ? 'bull' : 'bear'}>
                  {p.unrealized_pl >= 0 ? '+' : ''}{Number(p.unrealized_pl).toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!pnl.connected && (
        <div style={{ marginTop: 8, fontFamily: MONO, fontSize: 10, color: 'var(--yellow)' }}>
          ⚠ Broker disconnected — set ALPACA_API_KEY / ALPACA_SECRET_KEY / ALPACA_PAPER=true in .env
        </div>
      )}
    </div>
  )
}

export default function Desk() {
  const [tick, setTick] = useState(0)
  const status = usePoll(`/api/desk/status?t=${tick}`, 10_000)
  const slate = usePoll('/api/desk/slate', 30_000)
  const debates = usePoll('/api/desk/debates?n=8', 30_000)
  const execs = usePoll('/api/desk/executions?n=60', 15_000)
  const pnl = usePoll('/api/desk/pnl', 30_000)
  const [running, setRunning] = useState(false)

  const runNow = async () => {
    setRunning(true)
    try { await fetch('/api/desk/run-now', { method: 'POST' }) } finally {
      setTimeout(() => setRunning(false), 4000)
    }
  }

  const working = status?.working
  const day = status?.gates || {}

  return (
    <div>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 16, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontFamily: MONO, fontSize: 20, fontWeight: 800, color: 'var(--orange)', letterSpacing: '.2em', textShadow: 'var(--glow)' }}>
            THE DESK
          </div>
          <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--muted)', letterSpacing: '.2em' }}>
            ANALYSTS → DEBATE → RISK → SLATE → EXECUTION → REFLECTION
          </div>
        </div>
        <div style={{ flex: 1 }} />
        {status?.last_cycle?.regime && (
          <span className="pill-orange score-pill">{status.last_cycle.regime}</span>
        )}
        <span style={{ fontFamily: MONO, fontSize: 10, color: 'var(--text-dim)' }}>
          cycles <span className="white">{status?.cycle_count ?? 0}</span>
          {' · '}every <span className="white">{status?.config?.interval_minutes ?? '—'}m</span>
        </span>
        <button onClick={runNow} disabled={running || working} style={{
          fontFamily: MONO, fontSize: 11, fontWeight: 800, letterSpacing: '.1em',
          padding: '9px 18px', borderRadius: 5, cursor: 'pointer',
          background: 'var(--orange-bg)', color: 'var(--orange)',
          border: '1px solid var(--orange-dim)',
        }}>
          {working ? '◌ CYCLE RUNNING…' : '▶ RUN CYCLE NOW'}
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.6fr) minmax(280px, 1fr)', gap: 14 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
          <DebateTheatre debates={debates} />
          <Slate slate={slate} />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
          <AutoExecToggle status={status} onChanged={() => setTick(t => t + 1)} />
          <PaperPnL pnl={pnl} />
          <FillsFeed execs={execs} />
        </div>
      </div>

      <div style={{ marginTop: 14, fontFamily: MONO, fontSize: 9, color: 'var(--muted)', letterSpacing: '.08em', lineHeight: 1.8 }}>
        SAFETY CONTRACT: auto-execution is PAPER-ONLY and hard-gated in code — ALPACA_PAPER must equal "true",
        the toggle must be armed, budgets must have room, and a live account ALWAYS routes to manual approval.
        Every fill links to its debate transcript and evidence. Research &amp; education only — not financial advice.
      </div>
    </div>
  )
}
