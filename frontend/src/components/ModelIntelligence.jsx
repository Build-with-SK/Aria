/**
 * components/ModelIntelligence.jsx — which model's prediction may be trusted,
 * and how much, from /api/intelligence/models and /api/intelligence/debate.
 *
 * WHAT IT MAY SAY
 * A rate appears only when the backend MEASURED it (enough calls on enough
 * dates); otherwise the cell says INSUFFICIENT DATA with the n behind it. A
 * number that is not a probability is labelled by its type (CONFIDENCE,
 * SCORE, NONE) and never shown as a percentage chance. Every model is
 * UNCALIBRATED until a validated calibration exists, and the label says so.
 * Status chips come from the registry: evidence alone can only ever make a
 * model EXPERIMENTAL; VALIDATED / PRODUCTION are owner decisions behind the
 * promotion gate.
 */
import React from 'react'
import { useDebateValue, useModelIntelligence } from '../hooks/useApi'
import { SectionHeader } from './UI'

const mono = { fontFamily: 'var(--mono)' }

const STATUS_COLOR = {
  PRODUCTION: 'var(--green)', VALIDATED: 'var(--green)', EXPERIMENTAL: 'var(--orange)',
  INSUFFICIENT_DATA: 'var(--yellow)', NOT_RECORDED: 'var(--muted)', RETIRED: 'var(--muted)',
}

const Chip = ({ text, color = 'var(--muted)' }) => (
  <span style={{ ...mono, fontSize: 9, letterSpacing: '.08em', color, border: `1px solid ${color}`,
                 borderRadius: 3, padding: '1px 5px', marginRight: 5, whiteSpace: 'nowrap' }}>
    {text}
  </span>
)

const pct = x => (x == null ? '—' : `${(x * 100).toFixed(1)}%`)

function Rate({ block }) {
  if (!block) return <span style={{ color: 'var(--muted)' }}>—</span>
  if (block.status !== 'MEASURED') {
    return (
      <span style={{ color: 'var(--yellow)' }}>
        INSUFFICIENT DATA <span style={{ color: 'var(--muted)' }}>(n={block.n ?? 0})</span>
      </span>
    )
  }
  const ci = block.ci95_cluster || block.ci95_wilson
  return (
    <span>
      <span style={{ color: '#fff', fontWeight: 700 }}>{pct(block.rate)}</span>
      <span style={{ color: 'var(--muted)' }}>
        {' '}n={block.n} · {block.n_clusters} dates
        {ci ? ` · CI ${(ci[0] * 100).toFixed(0)}–${(ci[1] * 100).toFixed(0)}%` : ''}
      </span>
      <div style={{ fontSize: 9, color: 'var(--text-dim)' }}>{block.verdict}</div>
    </span>
  )
}

export default function ModelIntelligence() {
  const { data } = useModelIntelligence()
  const { data: debate } = useDebateValue()
  if (!data) return null
  const cards = data.registry || {}
  const evals = data.evaluations || {}
  const ds = data.dataset?.by_model || {}
  const table = data.routing_table || {}
  const routedFor = id => Object.entries(table)
    .filter(([, d]) => d.routed && d.route === id).map(([k]) => k).join(', ')

  return (
    <>
      <SectionHeader>MODEL INTELLIGENCE</SectionHeader>
      <div className="bb-card" style={{ marginBottom: 14 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '120px 130px 150px 1fr 1fr',
                      gap: 8, ...mono, fontSize: 9, color: 'var(--muted)', letterSpacing: '.12em',
                      borderBottom: '1px solid #141414', paddingBottom: 6 }}>
          <span>MODEL</span><span>STATUS</span><span>NUMBER IT STATES</span>
          <span>DIRECTIONAL RECORD</span><span>VS ALWAYS-LONG · ROUTING</span>
        </div>
        {Object.values(cards).map(c => {
          const ev = evals[c.ledger_source] || {}
          const long = ev.vs_always_long
          const d = ds[c.ledger_source] || {}
          return (
            <div key={c.model_id} style={{ display: 'grid',
                                           gridTemplateColumns: '120px 130px 150px 1fr 1fr',
                                           gap: 8, padding: '7px 0', borderBottom: '1px solid #101010',
                                           ...mono, fontSize: 10, alignItems: 'start' }}>
              <span>
                <span style={{ color: 'var(--orange)', fontWeight: 700 }}>{c.model_id.toUpperCase()}</span>
                <div style={{ fontSize: 9, color: 'var(--muted)' }}>
                  {c.code_hash_now ? `code ${c.code_hash_now}` : 'code hash —'}
                </div>
              </span>
              <span>
                <Chip text={(c.status || '').replace('_', ' ')} color={STATUS_COLOR[c.status]} />
                {c.owner_status_lapsed && <Chip text="EVIDENCE LAPSED" color="var(--red)" />}
              </span>
              <span>
                <Chip text={c.probability_type} />
                <div style={{ marginTop: 3 }}>
                  <Chip text={c.calibration?.calibration_version === 'none' || !c.calibration
                    ? 'UNCALIBRATED' : `CAL ${c.calibration.calibration_version}`}
                        color="var(--yellow)" />
                </div>
              </span>
              <span>
                <Rate block={ev.directional_accuracy} />
                {d.excluded > 0 && (
                  <div style={{ fontSize: 9, color: 'var(--muted)', marginTop: 2 }}>
                    {d.excluded} excluded · {d.included_via_regrade || 0} re-measured on one series
                  </div>
                )}
                {!c.ledger_source && (
                  <div style={{ fontSize: 9, color: 'var(--muted)' }}>never written to the ledger</div>
                )}
              </span>
              <span style={{ color: 'var(--text-dim)' }}>
                {long?.status === 'MEASURED'
                  ? <span>{long.verdict} <span style={{ color: 'var(--muted)' }}>(p={long.p_value})</span></span>
                  : <span style={{ color: 'var(--muted)' }}>not testable yet</span>}
                <div style={{ fontSize: 9, color: 'var(--muted)', marginTop: 2 }}>
                  {c.promotion_check?.eligible ? 'promotion gate passes'
                    : 'promotion gate: not met'}
                  {' · '}
                  {routedFor(c.model_id)
                    ? `routed for ${routedFor(c.model_id)}`
                    : 'never routed: default route (insufficient evidence)'}
                </div>
              </span>
            </div>
          )
        })}
        {debate?.evaluation?.conclusion && (
          <div style={{ ...mono, fontSize: 10, color: 'var(--text-dim)', marginTop: 10, lineHeight: 1.6 }}>
            <span style={{ color: 'var(--orange)' }}>DEBATE DESK · </span>
            {debate.evaluation.conclusion}
            {debate.evaluation.judge_vs_technical_analyst?.status === 'MEASURED' && (
              <span style={{ color: 'var(--muted)' }}>
                {' '}({debate.evaluation.events} graded events; judge vs its technical input on
                {' '}{debate.evaluation.judge_vs_technical_analyst.disagreements} disagreements,
                {' '}p={debate.evaluation.judge_vs_technical_analyst.p_value})
              </span>
            )}
          </div>
        )}
        <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 9, lineHeight: 1.65 }}>
          A rate is shown only above its sample floor, with an interval clustered by
          information date. Confidence and score values are not probabilities and are
          never scored as one. No model is promoted by its numbers: VALIDATED and
          PRODUCTION are owner decisions that the promotion gate must allow, and a
          prediction can only veto or defer a trade, never enlarge it.
        </div>
      </div>
    </>
  )
}
