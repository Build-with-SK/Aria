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

const mono = { fontFamily: 'var(--font)' }

const STATUS_COLOR = {
  PRODUCTION: 'var(--green)', VALIDATED: 'var(--green)', EXPERIMENTAL: 'var(--accent)',
  INSUFFICIENT_DATA: 'var(--yellow)', NOT_RECORDED: 'var(--ink-3)', RETIRED: 'var(--ink-3)',
}

const Chip = ({ text, color = 'var(--ink-3)' }) => (
  <span style={{ ...mono, fontSize: 12, color, border: `1px solid ${color}`,
                 borderRadius: 8, padding: '1px 5px', marginRight: 5, whiteSpace: 'nowrap' }}>
    {text}
  </span>
)

const pct = x => (x == null ? '—' : `${(x * 100).toFixed(1)}%`)

function Rate({ block }) {
  if (!block) return <span style={{ color: 'var(--ink-3)' }}>—</span>
  if (block.status !== 'MEASURED') {
    return (
      <span style={{ color: 'var(--yellow)' }}>
        Insufficient data <span style={{ color: 'var(--ink-3)' }}>(n={block.n ?? 0})</span>
      </span>
    )
  }
  const ci = block.ci95_cluster || block.ci95_wilson
  return (
    <span>
      <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{pct(block.rate)}</span>
      <span style={{ color: 'var(--ink-3)' }}>
        {' '}n={block.n} · {block.n_clusters} dates
        {ci ? ` · CI ${(ci[0] * 100).toFixed(0)}–${(ci[1] * 100).toFixed(0)}%` : ''}
      </span>
      <div style={{ fontSize: 12, color: 'var(--ink-2)' }}>{block.verdict}</div>
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
      <SectionHeader>Model intelligence</SectionHeader>
      <div className="bb-card" style={{ marginBottom: 14 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '120px 130px 150px 1fr 1fr',
                      gap: 8, ...mono, fontSize: 12, color: 'var(--ink-3)', 
                      borderBottom: '1px solid var(--inset)', paddingBottom: 6 }}>
          <span>Model</span><span>Status</span><span>Number it states</span>
          <span>Directional record</span><span>Vs always-long · routing</span>
        </div>
        {Object.values(cards).map(c => {
          const ev = evals[c.ledger_source] || {}
          const long = ev.vs_always_long
          const d = ds[c.ledger_source] || {}
          return (
            <div key={c.model_id} style={{ display: 'grid',
                                           gridTemplateColumns: '120px 130px 150px 1fr 1fr',
                                           gap: 8, padding: '7px 0', borderBottom: '1px solid var(--inset)',
                                           ...mono, fontSize: 12, alignItems: 'start' }}>
              <span>
                <span style={{ color: 'var(--accent)', fontWeight: 600 }}>{c.model_id.toUpperCase()}</span>
                <div style={{ fontSize: 12, color: 'var(--ink-3)' }}>
                  {c.code_hash_now ? `code ${c.code_hash_now}` : 'code hash —'}
                </div>
              </span>
              <span>
                <Chip text={(c.status || '').replace('_', ' ')} color={STATUS_COLOR[c.status]} />
                {c.owner_status_lapsed && <Chip text="Evidence lapsed" color="var(--red)" />}
              </span>
              <span>
                <Chip text={c.probability_type} />
                <div style={{ marginTop: 3 }}>
                  <Chip text={c.calibration?.calibration_version === 'none' || !c.calibration
                    ? 'Uncalibrated' : `Cal ${c.calibration.calibration_version}`}
                        color="var(--yellow)" />
                </div>
              </span>
              <span>
                <Rate block={ev.directional_accuracy} />
                {d.excluded > 0 && (
                  <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>
                    {d.excluded} excluded · {d.included_via_regrade || 0} re-measured on one series
                  </div>
                )}
                {!c.ledger_source && (
                  <div style={{ fontSize: 12, color: 'var(--ink-3)' }}>never written to the ledger</div>
                )}
              </span>
              <span style={{ color: 'var(--ink-2)' }}>
                {long?.status === 'MEASURED'
                  ? <span>{long.verdict} <span style={{ color: 'var(--ink-3)' }}>(p={long.p_value})</span></span>
                  : <span style={{ color: 'var(--ink-3)' }}>not testable yet</span>}
                <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>
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
          <div style={{ ...mono, fontSize: 12, color: 'var(--ink-2)', marginTop: 10, lineHeight: 1.6 }}>
            <span style={{ color: 'var(--accent)' }}>Debate desk · </span>
            {debate.evaluation.conclusion}
            {debate.evaluation.judge_vs_technical_analyst?.status === 'MEASURED' && (
              <span style={{ color: 'var(--ink-3)' }}>
                {' '}({debate.evaluation.events} graded events; judge vs its technical input on
                {' '}{debate.evaluation.judge_vs_technical_analyst.disagreements} disagreements,
                {' '}p={debate.evaluation.judge_vs_technical_analyst.p_value})
              </span>
            )}
          </div>
        )}
        <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', marginTop: 9, lineHeight: 1.65 }}>
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
