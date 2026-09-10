/**
 * pages/TrackRecordLedger.jsx — the record, from the ledger.
 *
 * WHY THIS EXISTS ALONGSIDE THE OLD PAGE
 * The previous TrackRecord read data/v5/predictions.jsonl, where every single
 * call carries a ~45-trading-day horizon. The file opened on 2026-08-01, so
 * nothing in it could be graded before 2026-09-28 — the page correctly showed
 * zero and would have kept showing zero for two more months.
 *
 * Meanwhile ARIA had already measured 306 graded directional calls in
 * data/desk/backfilled_outcomes.jsonl and 140 dated technical recommendations,
 * and nothing read either file. The ledger ingests all of it, so this page
 * opens with a real record instead of an honest zero.
 *
 * WHAT THE NUMBERS ARE ALLOWED TO SAY
 * An earlier version of this page reported a bare 44% hit rate and called the
 * desk's confidence "worse than uninformative". Both came from counting ledger
 * ROWS, and 80% of those rows were the same event re-logged — 306 rows are 61
 * distinct events. Corrected, the record is 46.6% over 116 independent events
 * with a 95% interval of 37.7–55.6%: indistinguishable from chance, which is a
 * much weaker statement and the one the sample supports.
 *
 * So the headline here is the INTERVAL, never the point estimate, and the
 * methodology panel states the deduplication and the population split before
 * any rate is shown. A rate without its n reads as a claim about skill.
 */
import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { useCalibration, useInvestigation, useLedger, useLedgerDecisions } from '../hooks/useApi'
import { Empty, SectionHeader, Spinner } from '../components/UI'

const mono = { fontFamily: 'var(--mono)' }

const Head = ({ label, value, sub, color = '#fff' }) => (
  <div className="bb-card" style={{ padding: '11px 14px' }}>
    <div style={{ ...mono, fontSize: 9, letterSpacing: '.15em', color: 'var(--muted)' }}>{label}</div>
    <div style={{ ...mono, fontSize: 21, fontWeight: 800, color, marginTop: 3 }}>{value}</div>
    {sub && <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 3, lineHeight: 1.5 }}>{sub}</div>}
  </div>
)

/* Reliability diagram, drawn as bars rather than a curve: with six buckets a
   curve implies a smoothness the data does not have. */
function Calibration({ cal }) {
  if (!cal) return null
  if (cal.measurable === false) {
    return (
      <div className="bb-card">
        <div style={{ ...mono, fontSize: 11, color: 'var(--muted)', lineHeight: 1.7 }}>{cal.note}</div>
      </div>
    )
  }
  const skill = cal.skill
  return (
    <div className="bb-card">
      <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', letterSpacing: '.15em' }}>BRIER</div>
          <div style={{ ...mono, fontSize: 18, fontWeight: 800, color: '#fff' }}>{cal.brier}</div>
        </div>
        <div>
          <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', letterSpacing: '.15em' }}>SKILL</div>
          <div style={{ ...mono, fontSize: 18, fontWeight: 800,
                        color: skill > 0 ? 'var(--green)' : 'var(--red)' }}>{skill}</div>
        </div>
        <div>
          <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', letterSpacing: '.15em' }}>ECE</div>
          <div style={{ ...mono, fontSize: 18, fontWeight: 800, color: '#fff' }}>{cal.ece}</div>
        </div>
        <div style={{ ...mono, fontSize: 10, color: 'var(--text-dim)', flex: '1 1 320px', lineHeight: 1.65 }}>
          {skill <= 0
            ? 'Negative skill means the stated confidence carries no information — '
              + 'you would score better ignoring it and saying 50% every time. Until '
              + 'that turns positive, confidence numbers from this source should not '
              + 'be used to size anything.'
            : 'Positive skill means the stated confidence carries information beyond '
              + 'a flat 50% guess.'}
        </div>
      </div>
      {cal.buckets.map(b => (
        <div key={b.range} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '3px 0' }}>
          <span style={{ ...mono, fontSize: 10, color: 'var(--muted)', minWidth: 84 }}>{b.range}</span>
          {!b.measurable ? (
            <span style={{ ...mono, fontSize: 9, color: 'var(--muted)' }}>
              {b.n} calls — too few to report
            </span>
          ) : (
            <>
              <div style={{ flex: 1, position: 'relative', height: 14, background: '#0d0d0d',
                            border: '1px solid var(--border)', borderRadius: 2 }}>
                {/* stated = where it claimed to be; realised = where it landed */}
                <div style={{ position: 'absolute', left: `${b.stated * 100}%`, top: 0,
                              width: 2, height: '100%', background: 'var(--muted)' }} />
                <div style={{ position: 'absolute', left: 0, top: 0, height: '100%',
                              width: `${b.realised * 100}%`,
                              background: Math.abs(b.gap) < 0.08 ? 'rgba(43,227,139,.35)'
                                                                 : 'rgba(255,85,96,.32)' }} />
              </div>
              <span style={{ ...mono, fontSize: 10, minWidth: 150, color: 'var(--text-dim)' }}>
                said {(b.stated * 100).toFixed(0)}% · was {(b.realised * 100).toFixed(0)}% (n={b.n})
              </span>
            </>
          )}
        </div>
      ))}
    </div>
  )
}

export default function TrackRecordLedger() {
  const { data: led, loading } = useLedger(300)
  const { data: cal } = useCalibration()
  const { data: inv } = useInvestigation()
  const { data: dec } = useLedgerDecisions(150)
  const [filter, setFilter] = useState('all')

  if (loading && !led) return <Spinner />

  const stats = led?.stats || {}
  const preds = led?.predictions || []
  const decisions = dec?.decisions || []
  const ov = inv?.overall || {}

  const shown = preds.filter(p =>
    filter === 'all' ? true
    : filter === 'graded' ? p.resolved
    : filter === 'pending' ? !p.resolved
    : p.source === filter)

  const sources = (stats.by_source || [])

  return (
    <div style={{ maxWidth: 1180, margin: '0 auto' }}>
      <div style={{ marginBottom: 12 }}>
        <div style={{ ...mono, fontSize: 16, fontWeight: 800, color: 'var(--orange)', letterSpacing: '.12em' }}>
          ◎ TRACK RECORD
        </div>
        <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
          Every falsifiable claim ARIA has made, and how it turned out.
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(170px,1fr))',
                    gap: 10, marginBottom: 14 }}>
        <Head label="RECORDED" value={stats.total ?? 0} sub="claims with a horizon" />
        <Head label="GRADED" value={stats.resolved ?? 0} color="var(--green)"
              sub={inv?.distinct_events != null
                    ? `${inv.distinct_events} independent · ${stats.pending ?? 0} pending`
                    : `${stats.pending ?? 0} still pending their horizon`} />
        {/* The headline is the INTERVAL, not the point estimate. A bare
            "44.6%" reads as a claim about skill; "46.6%, CI 37.7-55.6%,
            indistinguishable from chance" is what the sample supports. The
            first version of this page showed the bare rate over a population
            that was 80% duplicates. */}
        <Head label="HIT RATE"
              value={ov.rate != null ? `${(ov.rate * 100).toFixed(1)}%` : '—'}
              color={ov.rate == null ? 'var(--muted)'
                     : ov.verdict === 'better than chance' ? 'var(--green)'
                     : ov.verdict === 'worse than chance' ? 'var(--red)'
                     : 'var(--yellow)'}
              sub={ov.ci95
                    ? `95% CI ${(ov.ci95[0] * 100).toFixed(1)}–${(ov.ci95[1] * 100).toFixed(1)}% · ${ov.verdict}`
                    : (stats.hit_rate_note || `over ${stats.scored ?? 0} scored calls`)} />
        <Head label="NEXT DUE" value={stats.next_resolution_due || '—'}
              sub="the next horizon to elapse" />
        <Head label="DECISIONS" value={decisions.length}
              sub={Object.entries(stats.decisions || {})
                    .map(([k, v]) => `${v} ${k}`).join(' · ') || 'none recorded'} />
      </div>

      {/* ── per source, because these are different populations ── */}
      <SectionHeader>BY SOURCE</SectionHeader>
      <div className="bb-card" style={{ marginBottom: 14 }}>
        {sources.map(s => (
          <div key={s.source} style={{ display: 'flex', alignItems: 'center', gap: 12,
                                       padding: '6px 0', borderBottom: '1px solid #141414' }}>
            <span style={{ ...mono, fontSize: 11, fontWeight: 700, color: 'var(--orange)', minWidth: 92 }}>
              {s.source.toUpperCase()}
            </span>
            <span style={{ ...mono, fontSize: 10, color: 'var(--muted)', flex: 1 }}>
              {s.n} recorded · {s.resolved} graded
            </span>
            <span style={{ ...mono, fontSize: 12, fontWeight: 800,
                           color: s.hit_rate == null ? 'var(--muted)'
                                : s.hit_rate >= 0.5 ? 'var(--green)' : 'var(--red)' }}>
              {s.hit_rate != null ? `${(s.hit_rate * 100).toFixed(1)}%` : 'not yet measurable'}
            </span>
          </div>
        ))}
        <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 9, lineHeight: 1.65 }}>
          These are not comparable to each other. The desk rows are 1-day
          directional views and most were NO_TRADE verdicts the risk gates
          declined, so they measure opinion rather than traded edge. The
          technical rows are 10-day forward grades on indicator composites. The
          V5 rows are ~45-trading-day ensemble calls and have not reached their
          horizon yet.
        </div>
      </div>

      {/* ── methodology, stated before the numbers ── */}
      {inv && (
        <>
          <SectionHeader>HOW THIS IS COUNTED</SectionHeader>
          <div className="bb-card" style={{ marginBottom: 14 }}>
            <div style={{ ...mono, fontSize: 11, color: 'var(--text-dim)', lineHeight: 1.7 }}>
              Rates are computed over <strong style={{ color: '#fff' }}>distinct events</strong>,
              not ledger rows, and every one carries a Wilson interval.
              {inv.duplication?.note && (
                <>
                  {' '}<span style={{ color: 'var(--yellow)' }}>{inv.duplication.note}</span>
                </>
              )}
              {inv.duplication?.population_warning && (
                <>
                  {' '}<span style={{ color: 'var(--yellow)' }}>
                    {inv.duplication.population_warning}
                  </span>
                </>
              )}
            </div>
            <div style={{ marginTop: 10, borderTop: '1px solid #141414', paddingTop: 8 }}>
              {Object.entries(inv.by_population || {}).map(([name, p]) => (
                <div key={name} style={{ display: 'flex', gap: 12, alignItems: 'baseline',
                                         padding: '3px 0' }}>
                  <span style={{ ...mono, fontSize: 10, color: 'var(--muted)', minWidth: 110 }}>
                    {name}
                  </span>
                  <span style={{ ...mono, fontSize: 11, color: '#fff', minWidth: 62 }}>
                    n={p.n}
                  </span>
                  <span style={{ ...mono, fontSize: 11, color: 'var(--text-dim)', minWidth: 150 }}>
                    {p.rate != null ? `${(p.rate * 100).toFixed(1)}%` : '—'}
                    {p.ci95 ? ` (${(p.ci95[0] * 100).toFixed(0)}–${(p.ci95[1] * 100).toFixed(0)}%)` : ''}
                  </span>
                  <span style={{ ...mono, fontSize: 10, color: 'var(--muted)', flex: 1 }}>
                    {p.verdict}
                    {p.mean_confidence != null && ` · mean stated ${(p.mean_confidence * 100).toFixed(0)}%`}
                  </span>
                </div>
              ))}
            </div>
            {inv.confidence_semantics?.reading && (
              <div style={{ ...mono, fontSize: 10, color: 'var(--text-dim)', marginTop: 10,
                            borderTop: '1px solid #141414', paddingTop: 8, lineHeight: 1.65 }}>
                <span style={{ color: 'var(--orange)' }}>Is confidence a probability, or a ranking? </span>
                {inv.confidence_semantics.reading}
              </div>
            )}
          </div>
        </>
      )}

      {/* ── calibration (§32) ── */}
      <SectionHeader>CONFIDENCE CALIBRATION</SectionHeader>
      <div style={{ marginBottom: 14 }}>
        <Calibration cal={cal?.calibration} />
      </div>

      {/* ── the ledger itself ── */}
      <SectionHeader>THE LEDGER</SectionHeader>
      <div style={{ display: 'flex', gap: 7, marginBottom: 8, flexWrap: 'wrap' }}>
        {['all', 'graded', 'pending', ...sources.map(s => s.source)].map(f => (
          <button key={f} onClick={() => setFilter(f)}
            style={{ ...mono, fontSize: 10, letterSpacing: '.1em', padding: '5px 11px',
                     borderRadius: 4, cursor: 'pointer',
                     background: filter === f ? 'rgba(255,36,71,.14)' : '#0d0d0d',
                     border: `1px solid ${filter === f ? 'var(--orange)' : 'var(--border-2)'}`,
                     color: filter === f ? 'var(--orange)' : 'var(--text-dim)' }}>
            {f.toUpperCase()}
          </button>
        ))}
      </div>
      <div className="bb-card" style={{ maxHeight: 560, overflowY: 'auto' }}>
        {!shown.length && <Empty message="Nothing matches that filter." />}
        {shown.map(p => (
          <div key={p.id} style={{ padding: '7px 0', borderBottom: '1px solid #141414' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 9, flexWrap: 'wrap' }}>
              <Link to={`/research?symbol=${encodeURIComponent(p.subject)}`}
                    style={{ ...mono, fontSize: 11, fontWeight: 800, color: '#fff',
                             minWidth: 74, textDecoration: 'none' }}>
                {p.subject}
              </Link>
              <span className={`score-pill ${
                !p.resolved ? 'pill-neut' : p.correct ? 'pill-bull' : 'pill-bear'}`}
                style={{ fontSize: 9 }}>
                {!p.resolved ? 'PENDING' : p.correct == null ? 'UNSCORABLE'
                  : p.correct ? 'RIGHT' : 'WRONG'}
              </span>
              <span style={{ ...mono, fontSize: 10, color: 'var(--text-dim)', flex: 1 }}>
                {p.claim}
              </span>
              {p.probability != null && (
                <span style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
                  said {(p.probability * 100).toFixed(0)}%
                </span>
              )}
              {p.actual_return != null && (
                <span style={{ ...mono, fontSize: 10, fontWeight: 700,
                               color: p.actual_return >= 0 ? 'var(--green)' : 'var(--red)' }}>
                  {(p.actual_return * 100).toFixed(1)}%
                </span>
              )}
              <span style={{ ...mono, fontSize: 9, color: 'var(--muted)', minWidth: 78, textAlign: 'right' }}>
                {p.source} · {(p.created_at || '').slice(0, 10)}
              </span>
            </div>
            {p.outcome_note && (
              <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 2, paddingLeft: 83 }}>
                {p.outcome_note}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* ── decisions (§11) ── */}
      <SectionHeader>DECISIONS</SectionHeader>
      <div className="bb-card" style={{ maxHeight: 380, overflowY: 'auto' }}>
        {!decisions.length && <Empty message="No decisions recorded yet." />}
        {decisions.map(d => (
          <div key={d.id} style={{ display: 'flex', gap: 10, alignItems: 'baseline',
                                   padding: '5px 0', borderBottom: '1px solid #141414' }}>
            <span style={{ ...mono, fontSize: 9, color: 'var(--muted)', minWidth: 84 }}>
              {(d.at || '').slice(0, 10)}
            </span>
            <span style={{ ...mono, fontSize: 9, color: 'var(--orange)', minWidth: 62 }}>
              {d.kind}
            </span>
            <span style={{ ...mono, fontSize: 11, color: '#fff', minWidth: 66 }}>{d.subject || '—'}</span>
            <span style={{ ...mono, fontSize: 10, color: 'var(--text-dim)', flex: 1 }}>
              {d.action}{d.outcome ? ` — ${d.outcome}` : ''}
            </span>
            {d.pnl != null && (
              <span style={{ ...mono, fontSize: 10, fontWeight: 700,
                             color: d.pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                {d.pnl >= 0 ? '+' : ''}{Number(d.pnl).toFixed(2)}
              </span>
            )}
            <span style={{ ...mono, fontSize: 9, color: 'var(--muted)', minWidth: 62, textAlign: 'right' }}>
              {d.status}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
