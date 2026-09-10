/**
 * pages/V5.jsx
 * ARIA V5 — the multi-strategy research engine end to end: every module's
 * scored view with its own confidence interval, the ensemble that weighs them,
 * the risk gate that can veto them, the meta-reasoning that argues against the
 * conclusion, and the self-audit that closes it.
 *
 * Design rule for this page: never show a number without the uncertainty
 * attached to it. Dissent is rendered as prominently as the majority.
 */
import React, { useEffect, useState } from 'react'
import { TabBar } from '../components/UI'
import Term from '../components/Term'
import Compare from './Compare'
import { useCurrency } from '../currency/CurrencyContext'

const MONO = 'var(--mono)'

const VIEW_COLOR = {
  bull: 'var(--green)', bear: 'var(--red)',
  neutral: 'var(--muted)', abstain: '#5a5a66',
}
const VERDICT_COLOR = {
  APPROVE: 'var(--green)', REDUCE: 'var(--orange)',
  NO_TRADE: 'var(--muted)', VETO: 'var(--red)',
}

const pct = (x, d = 0) => (x == null ? '—' : `${(x * 100).toFixed(d)}%`)
const num = (x, d = 2) => (x == null ? '—' : Number(x).toFixed(d))

function Mono({ children, size = 11, color = 'var(--muted)', weight = 400, style }) {
  return <span style={{ fontFamily: MONO, fontSize: size, color, fontWeight: weight, ...style }}>{children}</span>
}

/* ── bull / neutral / bear mass, plus the confidence interval underneath ── */
function ScoreBar({ m }) {
  const total = (m.bull || 0) + (m.bear || 0) + (m.neutral || 0) || 100
  const seg = (n, c) => (n ? <div style={{ width: `${(n / total) * 100}%`, background: c, height: 7 }} /> : null)
  return (
    <div style={{ minWidth: 150 }}>
      <div style={{ display: 'flex', height: 7, borderRadius: 3, overflow: 'hidden', border: '1px solid var(--border)' }}>
        {seg(m.bull, 'var(--green)')}{seg(m.neutral, '#2a2a34')}{seg(m.bear, 'var(--red)')}
      </div>
      <div style={{ marginTop: 3 }}>
        <Mono size={9}>
          {m.ci_low != null
            ? `CI ${pct(m.ci_low)}–${pct(m.ci_high)} · n=${m.n_obs}`
            : 'no calibrated interval'}
        </Mono>
      </div>
    </div>
  )
}

function ModuleRow({ m, weight, onOpen }) {
  const c = VIEW_COLOR[m.view] || 'var(--muted)'
  return (
    <tr onClick={() => onOpen(m)} style={{ cursor: 'pointer' }}>
      <td className="white" style={{ fontWeight: 600 }}>{m.module}</td>
      <td><Mono size={9}>{m.family}</Mono></td>
      <td><Mono size={10} color={c} weight={800}>{m.view.toUpperCase()}</Mono></td>
      <td><Mono size={11} color={c} weight={700}>{m.net > 0 ? '+' : ''}{num(m.net, 1)}</Mono></td>
      <td><ScoreBar m={m} /></td>
      <td><Mono size={10}>{weight != null ? pct(weight, 1) : '—'}</Mono></td>
      <td style={{ maxWidth: 420 }}>
        <Mono size={10} color={m.insufficient_data ? '#5a5a66' : 'var(--text)'}>
          {m.insufficient_data ? m.reason : m.thesis}
        </Mono>
      </td>
    </tr>
  )
}

/* ── the drill-down: every cited fact and every declared weakness ── */
function ModuleDetail({ m, onClose }) {
  if (!m) return null
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,.72)', zIndex: 60,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        background: '#0b0810', border: '1px solid var(--border-2)', borderRadius: 6,
        maxWidth: 860, width: '100%', maxHeight: '84vh', overflowY: 'auto', padding: 20,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <div>
            <div className="white" style={{ fontSize: 15, fontWeight: 700 }}>{m.module}</div>
            <Mono size={10}>{m.family} · {m.horizon_days}-day horizon · as of {m.as_of}</Mono>
          </div>
          <Mono size={12} color={VIEW_COLOR[m.view]} weight={800}>
            {m.view.toUpperCase()} {m.net > 0 ? '+' : ''}{num(m.net, 1)}
          </Mono>
        </div>

        <p style={{ color: 'var(--text)', fontSize: 12.5, lineHeight: 1.65, marginTop: 12 }}>{m.thesis}</p>

        {!!(m.evidence || []).length && <>
          <div style={{ marginTop: 14, marginBottom: 6 }}><Mono size={10} color="var(--orange)" weight={800}>EVIDENCE — EVERY CLAIM CARRIES ITS SOURCE</Mono></div>
          {m.evidence.map((e, i) => (
            <div key={i} style={{ borderLeft: `2px solid ${VIEW_COLOR[e.lean] || '#2a2a34'}`, paddingLeft: 9, marginBottom: 7 }}>
              <div style={{ color: 'var(--text)', fontSize: 11.5 }}>{e.claim}</div>
              <Mono size={9}>source: {e.source}</Mono>
            </div>
          ))}
        </>}

        {!!(m.weaknesses || []).length && <>
          <div style={{ marginTop: 14, marginBottom: 6 }}><Mono size={10} color="var(--red)" weight={800}>WHAT THIS MODULE GETS WRONG</Mono></div>
          {m.weaknesses.filter(Boolean).map((w, i) => (
            <div key={i} style={{ color: 'var(--muted)', fontSize: 11.5, marginBottom: 5 }}>— {w}</div>
          ))}
        </>}

        <div style={{ marginTop: 16, textAlign: 'right' }}>
          <button onClick={onClose} style={{
            fontFamily: MONO, fontSize: 10, padding: '5px 14px', cursor: 'pointer',
            background: 'none', border: '1px solid var(--border-2)', color: 'var(--muted)', borderRadius: 4,
          }}>CLOSE</button>
        </div>
      </div>
    </div>
  )
}

/* ── headline ── */
function Headline({ a }) {
  const rec = a.recommendation
  const { price } = useCurrency()
  const col = VERDICT_COLOR[rec.risk_verdict] || 'var(--muted)'
  return (
    <div className="bb-card" style={{ borderLeft: `3px solid ${col}` }}>
      <div className="bb-card-header">RECOMMENDATION</div>
      <div style={{ padding: '10px 2px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: MONO, fontSize: 20, fontWeight: 800, color: col }}>{rec.action}</span>
          <Mono size={12} color="var(--text)">confidence {rec.confidence_band}</Mono>
          <Mono size={10}>({a.module_count.reporting}/{a.module_count.total} modules reporting · weights v{a.weights_version})</Mono>
        </div>
        <div style={{ color: 'var(--text)', fontSize: 12.5, lineHeight: 1.7, marginTop: 10 }}>{rec.headline}</div>
        <div style={{ marginTop: 10, display: 'flex', gap: 22, flexWrap: 'wrap' }}>
          {[['ENTRY', price(rec.entry, { symbol: a.ticker }).text],
            ['STOP', price(rec.stop, { symbol: a.ticker }).text],
            ['TARGET', price(rec.target, { symbol: a.ticker }).text],
            ['SIZE', `${num(rec.position_size_pct, 2)}%`], ['RISK', `${num(rec.max_loss_pct_of_equity, 2)}% of equity`]]
            .map(([k, v]) => (
              <div key={k}><Mono size={9}>{k}</Mono><div style={{ fontFamily: MONO, fontSize: 13, color: 'var(--text)' }}>{v}</div></div>
            ))}
        </div>
        <div style={{ marginTop: 12, padding: 9, background: 'rgba(255,255,255,.02)', border: '1px solid var(--border)', borderRadius: 4 }}>
          <Mono size={9} color="var(--orange)" weight={800}>INVALIDATION</Mono>
          <div style={{ color: 'var(--text)', fontSize: 11.5, marginTop: 4 }}>{rec.invalidation}</div>
        </div>
        <div style={{ marginTop: 8 }}><Mono size={9}>{rec.execution_note}</Mono></div>
      </div>
    </div>
  )
}

function EnsemblePanel({ e }) {
  return (
    <div className="bb-card">
      <div className="bb-card-header">ENSEMBLE SYNTHESIS</div>
      <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap', padding: '8px 2px' }}>
        {[[<Term k="net score">NET SCORE</Term>, `${e.net_score > 0 ? '+' : ''}${num(e.net_score, 1)}`],
          ['P(BULL)', pct(e.p_bull, 1)],
          [<Term k="calibration">CONFIDENCE</Term>, pct(e.confidence, 1)],
          [<Term k="dispersion">DISPERSION</Term>, num(e.dispersion, 1)],
          [<Term k="confidence interval">MEAN CI WIDTH</Term>, pct(e.mean_ci_width)],
          [<Term k="abstention">ABSTAINED</Term>, pct(e.abstention_rate)]].map(([k, v], i) => (
            <div key={i}><Mono size={9}>{k}</Mono><div style={{ fontFamily: MONO, fontSize: 15, color: 'var(--text)' }}>{v}</div></div>
          ))}
      </div>
      <div style={{ color: 'var(--muted)', fontSize: 11.5, lineHeight: 1.6, padding: '4px 2px' }}>
        {e.penalties?.explanation}
      </div>
      {!!(e.agreement?.conflicts || []).length && (
        <div style={{ marginTop: 8 }}>
          <Mono size={9} color="var(--orange)" weight={800}>OPEN CONFLICTS</Mono>
          {e.agreement.conflicts.map((c, i) => (
            <div key={i} style={{ color: 'var(--text)', fontSize: 11.5, marginTop: 4 }}>— {c}</div>
          ))}
        </div>
      )}
      {!!(e.dissent || []).length && (
        <div style={{ marginTop: 12 }}>
          <Mono size={9} color="var(--red)" weight={800}>
            {(e.agreement?.dissent_label || 'dissent').toUpperCase()} — PRESERVED, NOT DELETED
          </Mono>
          {e.dissent.map((d, i) => (
            <div key={i} style={{ marginTop: 5 }}>
              <Mono size={10} color="var(--text)" weight={700}>{d.module}</Mono>
              <Mono size={9}> ({d.family}, weight {pct(d.weight, 1)}, net {d.net > 0 ? '+' : ''}{num(d.net, 0)})</Mono>
              <div style={{ color: 'var(--muted)', fontSize: 11 }}>{d.strongest_evidence}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function RiskPanel({ r }) {
  const st = r.stress || {}
  return (
    <div className="bb-card">
      <div className="bb-card-header">RISK GATE — {r.verdict}</div>
      {(r.checks || []).map((c, i) => {
        const col = c.passed ? 'var(--green)' : (c.severity === 'veto' ? 'var(--red)' : 'var(--orange)')
        return (
          <div key={i} style={{ display: 'flex', gap: 9, padding: '5px 2px', borderBottom: '1px solid var(--border)' }}>
            <Mono size={9} color={col} weight={800} style={{ minWidth: 42 }}>
              {c.passed ? 'PASS' : (c.severity === 'veto' ? 'VETO' : 'FAIL')}
            </Mono>
            <div style={{ flex: 1 }}>
              <Mono size={10} color="var(--text)" weight={700}>{c.name}</Mono>
              <div style={{ color: 'var(--muted)', fontSize: 11 }}>{c.detail}</div>
            </div>
          </div>
        )
      })}
      {st.worst_month_pct != null && (
        <div style={{ marginTop: 10 }}>
          <Mono size={9} color="var(--orange)" weight={800}>STRESS</Mono>
          <div style={{ color: 'var(--muted)', fontSize: 11, marginTop: 4 }}>
            Worst day {num(st.worst_day_pct, 1)}% → {num(st.position_loss_worst_day_pct, 2)}% of equity ·
            worst month {num(st.worst_month_pct, 1)}% → {num(st.position_loss_worst_month_pct, 2)}% ·
            max drawdown {num(st.max_drawdown_pct, 1)}% · now {num(st.current_drawdown_pct, 1)}% off the high
          </div>
          {['market_5pct_drop', 'market_10pct_drop', 'market_20pct_drop'].filter(k => st[k]).map(k => (
            <div key={k} style={{ color: 'var(--muted)', fontSize: 11 }}>
              — market {k.split('_')[1].replace('pct', '%')} drop (beta {st[k].beta}) → instrument {num(st[k].instrument_move_pct, 1)}%,
              position {num(st[k].position_pnl_pct_of_equity, 2)}% of equity
            </div>
          ))}
        </div>
      )}
      {(r.notes || []).map((n, i) => <div key={i} style={{ marginTop: 6 }}><Mono size={9}>{n}</Mono></div>)}
    </div>
  )
}

function MetaPanel({ m }) {
  return (
    <div className="bb-card">
      <div className="bb-card-header">META-REASONING — THE CASE AGAINST</div>
      <div style={{ color: 'var(--text)', fontSize: 12, lineHeight: 1.7, padding: '6px 2px' }}>{m.counterargument}</div>

      {!!(m.contradictions || []).length && (
        <div style={{ marginTop: 10 }}>
          <Mono size={9} color="var(--red)" weight={800}>CONTRADICTIONS FOUND</Mono>
          {m.contradictions.map((c, i) => <div key={i} style={{ color: 'var(--muted)', fontSize: 11, marginTop: 4 }}>— {c}</div>)}
        </div>
      )}

      <div style={{ marginTop: 12 }}>
        <Mono size={9} color="var(--orange)" weight={800}>INDEPENDENT REASONING PATHS</Mono>
        {(m.alternative_paths || []).map((p, i) => (
          <div key={i} style={{ marginTop: 5 }}>
            <Mono size={10} color={p.agrees ? 'var(--green)' : 'var(--red)'} weight={700}>
              {p.name} → {p.direction} ({pct(p.p_bull)}) {p.agrees ? 'agrees' : 'DISAGREES'}
            </Mono>
            <div style={{ color: 'var(--muted)', fontSize: 11 }}>{p.detail}</div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 10, color: 'var(--text)', fontSize: 11.5 }}>{m.adjustment_reason}</div>

      <div style={{ marginTop: 12 }}>
        <Mono size={9} color="var(--orange)" weight={800}>FALSIFICATION TESTS</Mono>
        {(m.falsification_tests || []).map((t, i) => (
          <div key={i} style={{ color: 'var(--muted)', fontSize: 11, marginTop: 4 }}>{i + 1}. {t}</div>
        ))}
      </div>
    </div>
  )
}

function AuditPanel({ s }) {
  const cr = s.confidence_range || {}
  const Block = ({ title, items, color }) => (
    <div style={{ marginTop: 12 }}>
      <Mono size={9} color={color} weight={800}>{title}</Mono>
      {(items || []).map((x, i) => (
        <div key={i} style={{ color: 'var(--muted)', fontSize: 11, marginTop: 4 }}>— {x}</div>
      ))}
    </div>
  )
  return (
    <div className="bb-card">
      <div className="bb-card-header">SELF-AUDIT</div>
      <Block title="WHAT I KNOW" items={s.what_i_know} color="var(--green)" />
      <Block title="WHAT I DO NOT KNOW" items={s.what_i_do_not_know} color="var(--red)" />
      <div style={{ marginTop: 12 }}>
        <Mono size={9} color="var(--orange)" weight={800}>ASSUMPTIONS, EACH WITH ITS OWN CONFIDENCE</Mono>
        {(s.assumptions || []).map((a, i) => (
          <div key={i} style={{ marginTop: 5 }}>
            <div style={{ color: 'var(--text)', fontSize: 11.5 }}>{a.assumption}</div>
            <Mono size={9}>confidence: {a.confidence} · {a.basis}</Mono>
          </div>
        ))}
      </div>
      <Block title="WHAT WOULD CHANGE THIS CONCLUSION" items={s.what_would_change_this} color="var(--orange)" />
      <div style={{ marginTop: 12 }}>
        <Mono size={9} color="var(--orange)" weight={800}>CONFIDENCE</Mono>
        <div style={{ color: 'var(--text)', fontSize: 11.5, marginTop: 4 }}>
          {cr.direction} · P(bull) {pct(cr.p_bull_range?.[0])}–{pct(cr.p_bull_range?.[1])} ·
          confidence {pct(cr.confidence_range?.[0])}–{pct(cr.confidence_range?.[1])}
        </div>
        <Mono size={9}>{cr.derivation}</Mono>
      </div>
      <Block title="BLIND SPOTS AND MODEL WEAKNESSES" items={s.blind_spots} color="var(--red)" />
      {!!s.track_record?.n_predictions && (
        <div style={{ marginTop: 12 }}>
          <Mono size={9} color="var(--orange)" weight={800}>TRACK RECORD, STATED BEFORE THE NEXT CALL</Mono>
          <div style={{ color: 'var(--muted)', fontSize: 11, marginTop: 4 }}>
            Last {s.track_record.n_predictions} calls were {pct(s.track_record.bull_share)} bullish ·
            {' '}{s.track_record.n_resolved} resolved
            {s.track_record.hit_rate != null && ` at a ${pct(s.track_record.hit_rate)} hit rate`}
            {s.track_record.recent_streak && ` · recent: ${s.track_record.recent_streak}`}
          </div>
        </div>
      )}
    </div>
  )
}

/* ── learning tab ── */
function Learning() {
  const [d, setD] = useState(null)
  const [busy, setBusy] = useState(false)
  const load = () => fetch('/api/v5/learning').then(r => r.json()).then(setD).catch(() => {})
  // Block body: useEffect must not be handed a function that returns a promise —
  // React treats the return value as the cleanup and calls it on unmount.
  useEffect(() => { load() }, [])
  const resolve = () => {
    setBusy(true)
    fetch('/api/v5/learning/resolve', { method: 'POST' })
      .then(r => r.json()).then(() => load()).finally(() => setBusy(false))
  }
  if (!d) return <Mono>loading the prediction log…</Mono>
  const sc = d.module_scorecard || {}
  return (
    <>
      <div className="bb-card">
        <div className="bb-card-header">CONTINUOUS SELF-IMPROVEMENT</div>
        <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap', padding: '8px 2px' }}>
          {[['PREDICTIONS', d.total_predictions], ['RESOLVED', d.resolved], ['PENDING', d.pending],
            ['HIT RATE', pct(d.hit_rate, 1)], ['STATED CONFIDENCE', pct(d.mean_confidence, 1)],
            ['CALIBRATION GAP', d.calibration_gap == null ? '—' : `${(d.calibration_gap * 100).toFixed(1)}pp`],
            ['WEIGHTS', `v${d.weights_version}`]].map(([k, v]) => (
              <div key={k}><Mono size={9}>{k}</Mono><div style={{ fontFamily: MONO, fontSize: 15, color: 'var(--text)' }}>{v}</div></div>
            ))}
        </div>
        <button onClick={resolve} disabled={busy} style={{
          fontFamily: MONO, fontSize: 10, padding: '5px 14px', marginTop: 6, cursor: 'pointer',
          background: 'none', border: '1px solid var(--orange)', color: 'var(--orange)', borderRadius: 4,
        }}>{busy ? 'RESOLVING…' : 'RESOLVE ELAPSED PREDICTIONS'}</button>
        <div style={{ marginTop: 8 }}>
          <Mono size={9}>Weights move only after 25+ resolved calls for a module AND a significant binomial test. Every version is reversible.</Mono>
        </div>
      </div>

      {!!Object.keys(sc).length && (
        <div className="bb-card">
          <div className="bb-card-header">MODULE SCORECARD</div>
          <table>
            <thead><tr><th><Term>MODULE</Term></th><th>RIGHT</th><th>WRONG</th><th><Term k="hit rate">HIT RATE</Term></th><th><Term k="p-value">p-VALUE</Term></th><th><Term>MULTIPLIER</Term></th></tr></thead>
            <tbody>
              {Object.entries(sc).sort((a, b) => (b[1].n || 0) - (a[1].n || 0)).map(([name, r]) => (
                <tr key={name}>
                  <td className="white">{name}</td>
                  <td><Mono size={11} color="var(--green)">{r.right}</Mono></td>
                  <td><Mono size={11} color="var(--red)">{r.wrong}</Mono></td>
                  <td><Mono size={11}>{pct(r.hit_rate, 1)}</Mono></td>
                  <td><Mono size={11} color={r.p_value != null && r.p_value < 0.05 ? 'var(--orange)' : 'var(--muted)'}>{r.p_value ?? '—'}</Mono></td>
                  <td><Mono size={11}>{(d.multipliers || {})[name] ?? '1.0'}</Mono></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!!Object.keys(d.attribution_counts || {}).length && (
        <div className="bb-card">
          <div className="bb-card-header">OUTCOME ATTRIBUTION</div>
          {Object.entries(d.attribution_counts).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 2px' }}>
              <Mono size={11} color="var(--text)">{k}</Mono><Mono size={11}>{v}</Mono>
            </div>
          ))}
        </div>
      )}

      <div className="bb-card">
        <div className="bb-card-header">RECENT PREDICTIONS</div>
        <table>
          <thead><tr><th>AT</th><th>TICKER</th><th><Term>CALL</Term></th><th><Term>CONF</Term></th><th>STATUS</th><th>RETURN</th><th><Term>ATTRIBUTION</Term></th></tr></thead>
          <tbody>
            {(d.recent || []).map((p, i) => (
              <tr key={i}>
                <td><Mono size={9}>{(p.at || '').slice(0, 16)}</Mono></td>
                <td className="white">{p.ticker}</td>
                <td><Mono size={10} color={VIEW_COLOR[p.direction]} weight={700}>{(p.direction || '').toUpperCase()}</Mono></td>
                <td><Mono size={10}>{pct(p.confidence, 1)}</Mono></td>
                <td><Mono size={10} color={p.resolved ? (p.correct ? 'var(--green)' : 'var(--red)') : 'var(--muted)'}>
                  {p.resolved ? (p.correct ? 'CORRECT' : 'WRONG') : 'PENDING'}</Mono></td>
                <td><Mono size={10}>{p.realised_return == null ? '—' : pct(p.realised_return, 1)}</Mono></td>
                <td><Mono size={9}>{(p.attribution?.causes || []).join(', ')}</Mono></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

/* ── module catalogue ── */
function Catalogue() {
  const [d, setD] = useState(null)
  useEffect(() => { fetch('/api/v5/modules').then(r => r.json()).then(setD).catch(() => {}) }, [])
  if (!d) return <Mono>loading the module registry…</Mono>
  const byFamily = {}
  d.modules.forEach(m => { (byFamily[m.family] = byFamily[m.family] || []).push(m) })
  return (
    <>
      <div className="bb-card">
        <div className="bb-card-header">RESEARCH ENGINE — {d.count} INDEPENDENTLY CALLABLE MODULES</div>
        <div style={{ padding: '6px 2px' }}>
          <Mono size={10}>
            Family weights: {Object.entries(d.family_weights).map(([f, w]) => `${f} ${(w * 100).toFixed(0)}%`).join(' · ')}
          </Mono>
        </div>
      </div>
      {Object.entries(byFamily).map(([fam, mods]) => (
        <div className="bb-card" key={fam}>
          <div className="bb-card-header">{fam.toUpperCase()} · {mods.length}</div>
          <table>
            <thead><tr><th><Term>MODULE</Term></th><th><Term>HORIZON</Term></th><th>WHAT IT DOES</th></tr></thead>
            <tbody>
              {mods.map(m => (
                <tr key={m.name}>
                  <td className="white" style={{ fontWeight: 600 }}>
                    {m.name}{m.research_only && <Mono size={9} color="var(--orange)"> · research only, cannot vote</Mono>}
                  </td>
                  <td><Mono size={10}>{m.horizon_days}d</Mono></td>
                  <td><Mono size={10} color="var(--text)">{m.description}</Mono></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  )
}

/* ── page ── */
/**
 * §4 names this component's shape as the thing to stop building: Analysis,
 * Compare, Modules and Learning were four tabs over one research engine. They
 * are not four destinations — they are one analysis at four depths.
 *
 * `embedded` (with a `symbol`) renders ONLY the analysis, with no tab bar and
 * no second search box, so the Research workspace can own the symbol and show
 * this as the deep layer of one dossier. The standalone form is kept for the
 * /v5 route's own users but is no longer in the rail.
 */
export default function V5({ symbol: symbolProp, embedded = false }) {
  const [tab, setTab] = useState('analysis')
  const [symbol, setSymbol] = useState(symbolProp || 'AAPL')
  const [input, setInput] = useState(symbolProp || 'AAPL')
  const [a, setA] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState(null)

  const run = (sym) => {
    setLoading(true); setErr(''); setA(null)
    fetch(`/api/v5/analyze/${encodeURIComponent(sym)}`)
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j.detail || 'analysis failed')))
      .then(setA)
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false))
  }
  useEffect(() => { if (tab === 'analysis') run(symbol) }, [symbol, tab])
  // When the parent owns the symbol, follow it rather than keeping our own.
  useEffect(() => { if (symbolProp) { setSymbol(symbolProp); setInput(symbolProp) } }, [symbolProp])

  const submit = (e) => { e.preventDefault(); const s = input.trim().toUpperCase(); if (s) setSymbol(s) }

  return (
    <div>
      {!embedded && (
        <div style={{ marginBottom: 14 }}>
          <div className="white" style={{ fontSize: 17, fontWeight: 700, letterSpacing: '.04em' }}>ARIA V5</div>
          <Mono size={10}>
            Multi-strategy research engine · no single model ever speaks alone · confidence falls with disagreement
          </Mono>
        </div>
      )}

      {!embedded && (
        <TabBar
          tabs={[{ id: 'analysis', label: 'Analysis', icon: '◈' },
                 { id: 'compare', label: 'Compare', icon: '⚡' },
                 { id: 'modules', label: 'Modules', icon: '⚗' },
                 { id: 'learning', label: 'Learning', icon: '↻' }]}
          active={tab} onChange={setTab} />
      )}

      {tab === 'analysis' && (
        <>
          {!embedded && (
          <form onSubmit={submit} style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
            <input value={input} onChange={e => setInput(e.target.value)} placeholder="ticker — any global listing"
              style={{
                fontFamily: MONO, fontSize: 12, padding: '7px 11px', width: 260,
                background: '#0b0810', border: '1px solid var(--border-2)', color: 'var(--text)', borderRadius: 4,
              }} />
            <button type="submit" style={{
              fontFamily: MONO, fontSize: 11, fontWeight: 700, padding: '7px 18px', cursor: 'pointer',
              background: 'none', border: '1px solid var(--orange)', color: 'var(--orange)', borderRadius: 4,
            }}>RUN FULL ANALYSIS</button>
            {a && <a href={`/api/v5/report/${symbol}?format=md`} target="_blank" rel="noreferrer" style={{
              fontFamily: MONO, fontSize: 10, padding: '8px 10px', color: 'var(--muted)', textDecoration: 'none',
            }}>IC MEMO ↗</a>}
          </form>
          )}
          {embedded && a && (
            <div style={{ marginBottom: 10 }}>
              <a href={`/api/v5/report/${symbol}?format=md`} target="_blank" rel="noreferrer"
                 style={{ fontFamily: MONO, fontSize: 10, color: 'var(--orange)', textDecoration: 'none' }}>
                IC MEMO ↗
              </a>
            </div>
          )}

          {loading && <Mono>running {symbol} through the full chain — 40 modules, ensemble, meta-review, risk gate…</Mono>}
          {err && <div style={{ color: 'var(--red)', fontFamily: MONO, fontSize: 11 }}>{err}</div>}

          {a && <>
            <Headline a={a} />
            <EnsemblePanel e={a.ensemble} />
            <div className="bb-card">
              <div className="bb-card-header">
                EVERY MODULE — {a.module_count.reporting} REPORTING, {a.module_count.abstained} ABSTAINED
              </div>
              <table>
                <thead><tr><th><Term>MODULE</Term></th><th><Term>FAMILY</Term></th><th>VIEW</th><th><Term k="net score">NET</Term></th><th>BULL / NEUTRAL / BEAR</th><th><Term k="multiplier">WEIGHT</Term></th><th>THESIS</th></tr></thead>
                <tbody>
                  {[...a.modules].sort((x, y) => (a.ensemble.weights[y.module] || 0) - (a.ensemble.weights[x.module] || 0))
                    .map(m => <ModuleRow key={m.module} m={m} weight={a.ensemble.weights[m.module]} onOpen={setOpen} />)}
                </tbody>
              </table>
              <div style={{ padding: '6px 2px' }}><Mono size={9}>click any module for its evidence, sources and declared weaknesses</Mono></div>
            </div>
            <MetaPanel m={a.meta} />
            <RiskPanel r={a.risk} />
            <AuditPanel s={a.self_audit} />
            <div style={{ padding: '4px 2px 20px' }}>
              <Mono size={9}>
                {a.ticker} · generated {a.as_of} · {a.elapsed_ms}ms · prediction {a.prediction_id || 'not logged'}
              </Mono>
            </div>
          </>}
        </>
      )}

      {/* "Buy X or Y?" is the same question this page answers, asked about two
          names — a mode, not a separate destination. */}
      {tab === 'compare' && <Compare />}
      {tab === 'modules' && <Catalogue />}
      {tab === 'learning' && <Learning />}

      <ModuleDetail m={open} onClose={() => setOpen(null)} />
    </div>
  )
}
