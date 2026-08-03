/**
 * pages/TrackRecord.jsx — THE FLYWHEEL
 *
 * The one page that answers "should I trust this system?" without spin.
 *
 * Design rule, and it is the whole point of the page: when a number is not yet
 * measurable, show the empty frame and say what is missing. Do not hide the
 * chart, and do not fill it with a statistic drawn from three observations. A
 * track record page that only appears once the numbers look good is marketing.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { TabBar } from '../components/UI'
import Term from '../components/Term'
import ML from './ML'

const MONO = 'var(--mono)'
const pct = (x, d = 0) => (x == null ? '—' : `${(x * 100).toFixed(d)}%`)
const num = (x, d = 2) => (x == null ? '—' : Number(x).toFixed(d))

function Mono({ children, size = 11, color = 'var(--muted)', weight = 400, style }) {
  return <span style={{ fontFamily: MONO, fontSize: size, color, fontWeight: weight, ...style }}>{children}</span>
}

function Tile({ label, value, sub, color = 'var(--text)' }) {
  return (
    <div style={{ minWidth: 120 }}>
      <Mono size={9}>{label}</Mono>
      <div style={{ fontFamily: MONO, fontSize: 19, color, fontWeight: 700, lineHeight: 1.3 }}>{value}</div>
      {sub && <Mono size={9}>{sub}</Mono>}
    </div>
  )
}

/* ── the loop, and whether it is actually turning ── */
function Flywheel({ stages }) {
  return (
    <div className="bb-card">
      <div className="bb-card-header">THE LOOP</div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'stretch' }}>
        {stages.map((s, i) => {
          const col = s.turning ? 'var(--green)' : 'var(--muted)'
          return (
            <React.Fragment key={s.stage}>
              <div style={{
                flex: '1 1 190px', padding: 11, borderRadius: 5,
                border: `1px solid ${s.turning ? 'rgba(0,255,140,.25)' : 'var(--border)'}`,
                background: s.turning ? 'rgba(0,255,140,.03)' : 'rgba(255,255,255,.01)',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <Mono size={10} color={col} weight={800}>{s.stage}</Mono>
                  <Mono size={16} color={col} weight={700}>{s.count}</Mono>
                </div>
                <div style={{ color: 'var(--muted)', fontSize: 10.5, marginTop: 5, lineHeight: 1.5 }}>
                  {s.detail}
                </div>
                {s.blocked && (
                  <div style={{ color: 'var(--orange)', fontSize: 10, marginTop: 6, lineHeight: 1.5 }}>
                    ⚠ {s.blocked}
                  </div>
                )}
              </div>
              {i < stages.length - 1 && (
                <div style={{ display: 'flex', alignItems: 'center' }}>
                  <Mono size={14} color={s.turning ? 'var(--green)' : 'var(--border-2)'}>→</Mono>
                </div>
              )}
            </React.Fragment>
          )
        })}
      </div>
    </div>
  )
}

/* ── reliability diagram: stated confidence vs what actually happened ── */
function CalibrationCurve({ cal }) {
  const W = 460, H = 300, PAD_L = 44, PAD_B = 34, PAD_T = 14, PAD_R = 14
  const x = p => PAD_L + ((p - 0.5) / 0.5) * (W - PAD_L - PAD_R)
  const y = p => H - PAD_B - p * (H - PAD_B - PAD_T)
  const pts = (cal.buckets || []).filter(b => b.reportable)
  const maxN = Math.max(1, ...pts.map(b => b.n))

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxWidth: 520, height: 'auto' }}>
      {/* grid */}
      {[0, 0.25, 0.5, 0.75, 1].map(g => (
        <g key={g}>
          <line x1={PAD_L} y1={y(g)} x2={W - PAD_R} y2={y(g)} stroke="var(--border)" strokeWidth="1" />
          <text x={PAD_L - 7} y={y(g) + 3} textAnchor="end"
                fill="var(--muted)" fontSize="9" fontFamily="monospace">{(g * 100).toFixed(0)}%</text>
        </g>
      ))}
      {[0.5, 0.6, 0.7, 0.8, 0.9, 1].map(g => (
        <text key={g} x={x(g)} y={H - PAD_B + 14} textAnchor="middle"
              fill="var(--muted)" fontSize="9" fontFamily="monospace">{(g * 100).toFixed(0)}%</text>
      ))}

      {/* perfect calibration */}
      <line x1={x(0.5)} y1={y(0.5)} x2={x(1)} y2={y(1)}
            stroke="var(--orange)" strokeWidth="1.5" strokeDasharray="4 3" opacity="0.75" />
      <text x={x(0.87)} y={y(0.93)} fill="var(--orange)" fontSize="9" fontFamily="monospace">
        perfectly calibrated
      </text>

      {/* observed */}
      {pts.length > 1 && (
        <polyline fill="none" stroke="var(--green)" strokeWidth="2"
                  points={pts.map(b => `${x(b.stated_confidence)},${y(b.realised_frequency)}`).join(' ')} />
      )}
      {pts.map((b, i) => (
        <g key={i}>
          <circle cx={x(b.stated_confidence)} cy={y(b.realised_frequency)}
                  r={4 + 5 * (b.n / maxN)} fill="var(--green)" opacity="0.85" />
          <text x={x(b.stated_confidence)} y={y(b.realised_frequency) - 11} textAnchor="middle"
                fill="var(--muted)" fontSize="8" fontFamily="monospace">n={b.n}</text>
        </g>
      ))}

      <text x={PAD_L} y={12} fill="var(--muted)" fontSize="9" fontFamily="monospace">
        realised frequency ↑
      </text>
      <text x={W - PAD_R} y={H - 4} textAnchor="end" fill="var(--muted)" fontSize="9" fontFamily="monospace">
        stated confidence →
      </text>

      {!cal.measurable && (
        <g>
          <rect x={PAD_L} y={PAD_T} width={W - PAD_L - PAD_R} height={H - PAD_B - PAD_T}
                fill="rgba(0,0,0,.55)" />
          <text x={W / 2} y={H / 2 - 6} textAnchor="middle" fill="var(--orange)"
                fontSize="12" fontFamily="monospace" fontWeight="bold">
            NOT YET MEASURABLE
          </text>
          <text x={W / 2} y={H / 2 + 13} textAnchor="middle" fill="var(--muted)"
                fontSize="10" fontFamily="monospace">
            {cal.n_resolved} of {cal.n_required} resolved calls
          </text>
        </g>
      )}
    </svg>
  )
}

function Calibration({ cal }) {
  return (
    <div className="bb-card">
      <div className="bb-card-header">CALIBRATION — DOES 60% ACTUALLY MEAN 60%?</div>
      <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap' }}>
        <CalibrationCurve cal={cal} />
        <div style={{ flex: '1 1 260px', minWidth: 240 }}>
          <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap', marginBottom: 12 }}>
            <Tile label={<Term k="brier score">BRIER SCORE</Term>} value={num(cal.brier, 3)}
                  sub={cal.brier_baseline ? `coin flip = ${num(cal.brier_baseline, 3)}` : 'lower is better'} />
            <Tile label={<Term k="skill">SKILL</Term>} value={pct(cal.skill, 0)}
                  color={cal.skill == null ? 'var(--muted)' : cal.skill > 0 ? 'var(--green)' : 'var(--red)'}
                  sub="vs always saying 50%" />
            <Tile label={<Term k="ece">AVG GAP (ECE)</Term>} value={pct(cal.ece, 1)}
                  color={cal.ece == null ? 'var(--muted)' : cal.ece <= 0.05 ? 'var(--green)' : 'var(--orange)'}
                  sub="claimed vs realised" />
          </div>
          <div style={{ color: 'var(--text)', fontSize: 11.5, lineHeight: 1.65 }}>
            {cal.verdict || cal.note}
          </div>
          {!!(cal.buckets || []).length && (
            <table style={{ marginTop: 12 }}>
              <thead><tr><th>BAND</th><th>N</th><th>CLAIMED</th><th>HAPPENED</th><th>GAP</th></tr></thead>
              <tbody>
                {cal.buckets.map((b, i) => (
                  <tr key={i}>
                    <td><Mono size={10} color="var(--text)">{b.range}</Mono></td>
                    <td><Mono size={10}>{b.n}</Mono></td>
                    <td><Mono size={10}>{pct(b.stated_confidence, 1)}</Mono></td>
                    <td><Mono size={10} color={b.reportable ? 'var(--text)' : 'var(--muted)'}>
                      {b.reportable ? pct(b.realised_frequency, 1) : 'below minimum'}</Mono></td>
                    <td><Mono size={10} color={b.gap == null ? 'var(--muted)'
                      : Math.abs(b.gap) <= 0.05 ? 'var(--green)' : 'var(--orange)'}>
                      {b.gap == null ? '—' : `${(b.gap * 100).toFixed(1)}pp`}</Mono></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}

/* ── the four independent loops feeding the record ── */
function Sources({ sources }) {
  return (
    <div className="bb-card">
      <div className="bb-card-header">WHERE THE LABELS COME FROM — {sources.filter(s => s.live).length}/{sources.length} LOOPS LIVE</div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {sources.map(s => (
          <div key={s.id} style={{
            flex: '1 1 230px', padding: 11, borderRadius: 5,
            border: `1px solid ${s.live ? 'var(--border-2)' : 'var(--border)'}`,
            opacity: s.live ? 1 : 0.62,
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <Mono size={11} color="var(--text)" weight={700}>{s.name}</Mono>
              <Mono size={9} color={s.live ? 'var(--green)' : 'var(--muted)'}>
                {s.live ? '● LIVE' : '○ IDLE'}
              </Mono>
            </div>
            <div style={{ color: 'var(--muted)', fontSize: 10.5, marginTop: 5, lineHeight: 1.5 }}>
              {s.what_it_labels}
            </div>
            <div style={{ marginTop: 7, display: 'flex', gap: 14 }}>
              <div><Mono size={9}>LOGGED</Mono><div style={{ fontFamily: MONO, fontSize: 13, color: 'var(--text)' }}>{s.total ?? 0}</div></div>
              <div><Mono size={9}>LABELLED</Mono><div style={{ fontFamily: MONO, fontSize: 13, color: 'var(--green)' }}>{s.resolved ?? 0}</div></div>
              {s.hit_rate != null && (
                <div><Mono size={9}>HIT RATE</Mono><div style={{ fontFamily: MONO, fontSize: 13, color: 'var(--text)' }}>{pct(s.hit_rate, 1)}</div></div>
              )}
            </div>
            <div style={{ marginTop: 6 }}>
              <Mono size={9}>→ {s.produces}</Mono>
            </div>
            {(s.note || s.error) && (
              <div style={{ marginTop: 5 }}><Mono size={9} color="var(--orange)">{s.note || s.error}</Mono></div>
            )}
            {s.next_resolution && (
              <div style={{ marginTop: 5 }}><Mono size={9}>next resolution {s.next_resolution}</Mono></div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

/* ── per-module skill, and the weight changes it earned ── */
function ModuleSkill({ perf, onRevert }) {
  const sc = perf.module_scorecard || {}
  const rows = Object.entries(sc).sort((a, b) => (b[1].n || 0) - (a[1].n || 0))
  return (
    <>
      <div className="bb-card">
        <div className="bb-card-header">PER-MODULE SKILL</div>
        {!rows.length && (
          <div style={{ padding: '8px 2px' }}>
            <Mono size={11}>
              No module has a scored call yet. A module is only graded when it took a side —
              abstentions and neutral reads never count either way.
            </Mono>
          </div>
        )}
        {!!rows.length && (
          <table>
            <thead><tr><th>MODULE</th><th>RIGHT</th><th>WRONG</th><th>HIT RATE</th><th><Term k="p-value">p-VALUE</Term></th><th>SIGNIFICANT?</th><th>WEIGHT ×</th></tr></thead>
            <tbody>
              {rows.map(([name, r]) => {
                const n = r.n || 0
                const sig = r.p_value != null && r.p_value < 0.05 && n >= 25
                // Name the condition that actually failed — "needs 25+ calls"
                // is misleading for a module that already has 61 of them.
                const why = sig ? 'YES — weight may move'
                  : n < 25 ? `no — needs 25+ scored calls (has ${n})`
                  : `no — p=${r.p_value}, not distinguishable from luck`
                return (
                  <tr key={name}>
                    <td className="white" style={{ fontWeight: 600 }}>{name}</td>
                    <td><Mono size={11} color="var(--green)">{r.right}</Mono></td>
                    <td><Mono size={11} color="var(--red)">{r.wrong}</Mono></td>
                    <td><Mono size={11} color="var(--text)">{pct(r.hit_rate, 1)}</Mono></td>
                    <td><Mono size={11}>{r.p_value ?? '—'}</Mono></td>
                    <td><Mono size={10} color={sig ? 'var(--orange)' : 'var(--muted)'}>{why}</Mono></td>
                    <td><Mono size={11}>{(perf.multipliers || {})[name] ?? '1.00'}</Mono></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="bb-card">
        <div className="bb-card-header">WEIGHT HISTORY — MEASURABLE, REVERSIBLE, VERSIONED</div>
        <div style={{ padding: '6px 2px' }}>
          <Mono size={10}>
            Currently at v{perf.weights_version}. A weight only moves after 25+ resolved calls for
            that module AND a binomial test at p&lt;0.05. Every version is kept and can be restored.
          </Mono>
        </div>
        {!(perf.weight_history || []).length && (
          <div style={{ padding: '4px 2px' }}><Mono size={11}>No weight has ever changed. That is the expected state until the evidence earns it.</Mono></div>
        )}
        {(perf.weight_history || []).map((h, i) => (
          <div key={i} style={{ borderTop: '1px solid var(--border)', padding: '7px 2px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Mono size={10} color="var(--text)" weight={700}>v{h.version} → replaced {String(h.replaced_at || '').slice(0, 16)}</Mono>
              <button onClick={() => onRevert(h.version)} style={{
                fontFamily: MONO, fontSize: 9, padding: '3px 10px', cursor: 'pointer',
                background: 'none', border: '1px solid var(--border-2)', color: 'var(--muted)', borderRadius: 3,
              }}>REVERT TO v{h.version}</button>
            </div>
            {(h.changes || []).map((c, j) => (
              <div key={j} style={{ color: 'var(--muted)', fontSize: 10.5, marginTop: 3 }}>
                {c.note ? c.note : `${c.module}: ${c.from} → ${c.to} (hit rate ${pct(c.hit_rate, 1)} over ${c.n} calls, p=${c.p_value})`}
              </div>
            ))}
          </div>
        ))}
      </div>
    </>
  )
}

/* ── why it was wrong, not just that it was ── */
function Attribution({ perf, lessons }) {
  const counts = perf.attribution_counts || {}
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1])
  const total = entries.reduce((s, [, v]) => s + v, 0) || 1
  return (
    <>
      <div className="bb-card">
        <div className="bb-card-header">WHY OUTCOMES LANDED WHERE THEY DID</div>
        {!entries.length && (
          <div style={{ padding: '8px 2px' }}>
            <Mono size={11}>
              Nothing attributed yet. Every resolved call is assigned a specific cause —
              market randomness, incorrect assumptions, weak features, poor model selection,
              data quality, regime change, behavioural bias, or a risk decision — never a
              generic "the market moved".
            </Mono>
          </div>
        )}
        {entries.map(([k, v]) => (
          <div key={k} style={{ padding: '4px 2px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <Mono size={11} color="var(--text)">{k}</Mono>
              <Mono size={11}>{v} ({pct(v / total, 0)})</Mono>
            </div>
            <div style={{ height: 4, background: 'var(--border)', borderRadius: 2, marginTop: 3 }}>
              <div style={{ width: `${(v / total) * 100}%`, height: 4, background: 'var(--orange)', borderRadius: 2 }} />
            </div>
          </div>
        ))}
      </div>

      {!!lessons.length && (
        <div className="bb-card">
          <div className="bb-card-header">WHAT IT WROTE DOWN AFTERWARDS</div>
          {lessons.map((l, i) => (
            <div key={i} style={{ borderTop: i ? '1px solid var(--border)' : 'none', padding: '8px 2px' }}>
              <Mono size={10} color="var(--orange)" weight={700}>
                {l.ticker || 'general'} · {String(l.at || '').slice(0, 16)}
              </Mono>
              <div style={{ color: 'var(--text)', fontSize: 11.5, marginTop: 4, lineHeight: 1.6 }}>
                {l.lesson || l.text || JSON.stringify(l).slice(0, 300)}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

/* ── the ledger ── */
function Ledger({ perf }) {
  const rows = perf.recent || []
  return (
    <div className="bb-card">
      <div className="bb-card-header">THE LEDGER — EVERY CALL, WON OR LOST</div>
      {!rows.length && <div style={{ padding: '8px 2px' }}><Mono size={11}>No predictions logged yet.</Mono></div>}
      {!!rows.length && (
        <table>
          <thead><tr><th>AT</th><th>TICKER</th><th>CALL</th><th>CONFIDENCE</th><th>HORIZON</th><th>OUTCOME</th><th>PRICE MOVE</th><th>ATTRIBUTED TO</th></tr></thead>
          <tbody>
            {rows.map((p, i) => (
              <tr key={i}>
                <td><Mono size={9}>{String(p.at || '').slice(0, 16)}</Mono></td>
                <td>
                  <Link to={`/research?symbol=${encodeURIComponent(p.ticker)}`}
                    title={`Open ${p.ticker} research`}
                    style={{ fontWeight: 600, color: 'var(--orange)', textDecoration: 'none' }}>
                    {p.ticker}
                  </Link>
                </td>
                <td><Mono size={10} weight={700}
                  color={p.direction === 'bull' ? 'var(--green)' : p.direction === 'bear' ? 'var(--red)' : 'var(--muted)'}>
                  {String(p.direction || '').toUpperCase()}</Mono></td>
                <td><Mono size={10}>{pct(p.confidence, 1)}</Mono></td>
                <td><Mono size={10}>{p.horizon_days}d</Mono></td>
                <td><Mono size={10} weight={700}
                  color={!p.resolved ? 'var(--muted)' : p.correct ? 'var(--green)' : 'var(--red)'}>
                  {p.resolved ? (p.correct ? 'CORRECT' : 'WRONG') : `PENDING → ${p.resolve_after || ''}`}</Mono></td>
                {/* This column is the raw price move, not P&L. A correct BEAR
                    call has a NEGATIVE move, so colouring by sign would paint
                    the system's best short calls red. Colour by whether the
                    move vindicated the call. */}
                <td><Mono size={10} color={!p.resolved ? 'var(--muted)'
                  : p.correct ? 'var(--green)' : 'var(--red)'}>
                  {p.realised_return == null ? '—' : pct(p.realised_return, 1)}</Mono></td>
                <td><Mono size={9}>{((p.attribution || {}).causes || []).join(', ') || '—'}</Mono></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

/* ── page ── */
export default function TrackRecord() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const [tab, setTab] = useState('record')

  const load = useCallback(() => {
    fetch('/api/v5/track-record')
      .then(r => (r.ok ? r.json() : Promise.reject('track record unavailable')))
      .then(setD).catch(e => setErr(String(e)))
  }, [])
  useEffect(() => { load() }, [load])

  const resolve = () => {
    setBusy('resolve')
    fetch('/api/v5/learning/resolve', { method: 'POST' })
      .then(r => r.json()).then(() => load()).finally(() => setBusy(''))
  }
  const revert = (v) => {
    setBusy('revert')
    fetch(`/api/v5/learning/revert/${v}`, { method: 'POST' })
      .then(r => r.json()).then(() => load()).finally(() => setBusy(''))
  }

  if (err) return <div style={{ color: 'var(--red)', fontFamily: MONO, fontSize: 12 }}>{err}</div>
  if (!d) return <Mono>loading the track record…</Mono>

  const h = d.headline
  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <div className="white" style={{ fontSize: 17, fontWeight: 700, letterSpacing: '.04em' }}>TRACK RECORD</div>
        <Mono size={10}>
          Every prediction ARIA has made, what actually happened, and what it changed as a result
        </Mono>
      </div>

      {/* the verdict, before anything else */}
      <div className="bb-card" style={{ borderLeft: '3px solid var(--orange)' }}>
        <div className="bb-card-header">THE HONEST ANSWER</div>
        <div style={{ color: 'var(--text)', fontSize: 13, lineHeight: 1.75, padding: '6px 2px' }}>{h.text}</div>
        <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap', marginTop: 10 }}>
          <Tile label="RESOLVED CALLS" value={h.resolved} />
          <Tile label={<Term k="hit rate">HIT RATE</Term>} value={pct(h.hit_rate, 1)} />
          <Tile label="STATED CONFIDENCE" value={pct(h.stated_confidence, 1)} />
          <Tile label={<Term k="calibration gap">CALIBRATION GAP</Term>}
                value={h.calibration_gap == null ? '—' : `${(h.calibration_gap * 100).toFixed(1)}pp`}
                color={h.calibration_gap == null ? 'var(--muted)'
                  : Math.abs(h.calibration_gap) <= 0.05 ? 'var(--green)' : 'var(--orange)'} />
          <Tile label="LABELLED OUTCOMES" value={h.total_labelled_outcomes} sub="across all four loops" />
        </div>
        <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button onClick={resolve} disabled={!!busy} style={{
            fontFamily: MONO, fontSize: 10, fontWeight: 700, padding: '6px 14px', cursor: 'pointer',
            background: 'none', border: '1px solid var(--orange)', color: 'var(--orange)', borderRadius: 4,
          }}>{busy === 'resolve' ? 'SCORING…' : 'SCORE ELAPSED PREDICTIONS'}</button>
          <a href="/api/v5/track-record/export?format=jsonl" target="_blank" rel="noreferrer" style={{
            fontFamily: MONO, fontSize: 10, fontWeight: 700, padding: '6px 14px', textDecoration: 'none',
            border: '1px solid var(--border-2)', color: 'var(--muted)', borderRadius: 4,
          }}>EXPORT TRAINING ROWS (JSONL) ↗</a>
        </div>
      </div>

      <Flywheel stages={d.flywheel} />

      <TabBar
        tabs={[{ id: 'record', label: 'Calibration', icon: '◎' },
               { id: 'skill', label: 'Module skill', icon: '⚖' },
               { id: 'why', label: 'Attribution', icon: '⌕' },
               { id: 'ledger', label: 'Ledger', icon: '≡' },
               { id: 'models', label: 'Model predictions', icon: '◉' }]}
        active={tab} onChange={setTab} />

      {tab === 'record' && <><Calibration cal={d.calibration} /><Sources sources={d.sources} /></>}
      {tab === 'skill' && <ModuleSkill perf={d.performance} onRevert={revert} />}
      {tab === 'why' && <Attribution perf={d.performance} lessons={d.lessons || []} />}
      {tab === 'ledger' && <Ledger perf={d.performance} />}
      {/* The ML ensemble's live predictions. They sit here rather than on their
          own page because a prediction without its outcome is half a story, and
          the other half is on the tabs to the left. */}
      {tab === 'models' && <ML />}

      <div style={{ padding: '4px 2px 20px' }}>
        <Mono size={9}>
          as of {d.as_of} · a prediction counts once per ticker, direction and day · research and
          education only, not financial advice
        </Mono>
      </div>
    </div>
  )
}
