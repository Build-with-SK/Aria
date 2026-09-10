/**
 * components/LiveCognition.jsx
 *
 * Watching ARIA reason — a SECTION of the Brain, not a destination.
 *
 * This replaces the page that called itself "ARIA · LIVE MIND" and streamed
 * the model's own narration through a typewriter. Two things changed, and both
 * were deliberate:
 *
 * 1. It is no longer a place you can go. There is one intelligence; watching it
 *    think is part of looking at it, not a second mind with its own nav entry.
 *
 * 2. It shows ACTIVITY, not chain-of-thought. The rows say "Reviewing the macro
 *    regime", "Comparing technical and ML evidence" and what each phase
 *    CONCLUDED. The intermediate narration stays server-side — the boundary is
 *    drawn once, in src/brain/aggregate.phase_activity(), so it cannot be
 *    quietly moved by a component.
 *
 * Conclusions ARE shown. They are the output of a step, not its working, and a
 * user who cannot see conclusions cannot audit the system at all.
 */
import React from 'react'

const mono = { fontFamily: 'var(--mono)' }

const PHASE_COLOR = {
  ORIENT: '#22d3ee', FOCUS: '#a78bfa', RECALL: '#f472b6',
  ANALYSE: '#60a5fa', DECIDE: '#34d399', REFLECT: '#fbbf24',
}

const STATUS_COLOR = {
  THINKING: 'var(--green)', ANALYSING: 'var(--blue)',
  WAITING: 'var(--yellow)', ERROR: 'var(--red)', IDLE: 'var(--muted)',
}

export function StatusDot({ status }) {
  const c = STATUS_COLOR[status] || 'var(--muted)'
  const live = status === 'THINKING' || status === 'ANALYSING'
  return (
    <span style={{
      display: 'inline-block', width: 7, height: 7, borderRadius: '50%',
      background: c, boxShadow: `0 0 8px ${c}`, marginRight: 7,
      animation: live ? 'corePulse 1.6s ease-in-out infinite' : 'none',
    }} />
  )
}

export default function LiveCognition({ cognition, status, statusReason, compact }) {
  const steps = cognition?.steps || []

  return (
    <div className="bb-card" style={{ marginBottom: 14 }}>
      <div className="bb-card-header" style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
      }}>
        <span>LIVE COGNITION</span>
        <span style={{ ...mono, fontSize: 9, color: 'var(--muted)', letterSpacing: '.1em' }}>
          {cognition?.cycle_count != null ? `CYCLE ${cognition.cycle_count}` : ''}
        </span>
      </div>

      <div style={{ ...mono, fontSize: 10, color: 'var(--text-dim)', marginBottom: 10 }}>
        <StatusDot status={status} />{statusReason}
      </div>

      {!steps.length && (
        <div style={{ ...mono, fontSize: 10, color: 'var(--muted)', lineHeight: 1.7 }}>
          No reasoning cycle has been recorded yet. This shows what ARIA was
          doing during its last pass — it stays empty rather than animating
          something that did not happen.
        </div>
      )}

      <div style={{ maxHeight: compact ? 240 : 420, overflowY: 'auto' }}>
        {steps.map((s, i) => {
          const c = PHASE_COLOR[s.phase] || 'var(--muted)'
          return (
            <div key={i} style={{
              paddingLeft: 11, marginBottom: 9, borderLeft: `2px solid ${c}`,
            }}>
              <div style={{
                ...mono, fontSize: 9, fontWeight: 800, letterSpacing: '.14em',
                color: c,
              }}>
                {s.phase || 'STEP'}
              </div>
              <div style={{ ...mono, fontSize: 11, color: 'var(--text)', marginTop: 1 }}>
                {s.activity}
              </div>
              {s.conclusion && (
                <div style={{
                  ...mono, fontSize: 10, color: 'var(--text-dim)',
                  marginTop: 3, lineHeight: 1.55,
                }}>
                  → {s.conclusion}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {!!(cognition?.decisions || []).length && (
        <div style={{ marginTop: 10, paddingTop: 9, borderTop: '1px solid #141414' }}>
          <div style={{ ...mono, fontSize: 9, fontWeight: 800, letterSpacing: '.14em',
            color: 'var(--green)', marginBottom: 5 }}>
            DECISIONS · {cognition.trades_queued || 0} QUEUED FOR APPROVAL
          </div>
          {cognition.decisions.map((d, i) => (
            <div key={i} style={{ ...mono, fontSize: 10, lineHeight: 1.7, color: 'var(--text-dim)' }}>
              <span style={{ color: '#fff', fontWeight: 700 }}>{d.ticker}</span>
              {' · '}
              <span style={{
                color: d.action === 'PROPOSE_BUY' ? 'var(--green)'
                  : d.action === 'PROPOSE_SELL' ? 'var(--red)' : 'var(--muted)',
                fontWeight: 700,
              }}>{d.action}</span>
              {d.reason ? ` · ${d.reason}` : ''}
            </div>
          ))}
        </div>
      )}

      <div style={{ ...mono, fontSize: 8, color: 'var(--muted)', marginTop: 10,
        lineHeight: 1.6 }}>
        {cognition?.note || 'Activity summaries, not internal reasoning.'}
        {' '}The brain proposes; the human approves.
      </div>
    </div>
  )
}
