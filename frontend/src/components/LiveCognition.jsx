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

const PHASE_COLOR = {
  ORIENT: 'var(--cyan)', FOCUS: 'var(--purple)', RECALL: 'var(--pink)',
  ANALYSE: 'var(--blue)', DECIDE: 'var(--green)', REFLECT: 'var(--yellow)',
}

const STATUS_COLOR = {
  THINKING: 'var(--green)', ANALYSING: 'var(--blue)',
  WAITING: 'var(--yellow)', ERROR: 'var(--red)', IDLE: 'var(--ink-3)',
}

/* Phase and status names arrive as the server's own words (ORIENT, THINKING).
   They are shown in sentence case; the value itself is never changed. */
export const sentence = word => {
  const s = String(word || '').replace(/_/g, ' ').toLowerCase()
  return s ? s[0].toUpperCase() + s.slice(1) : ''
}

export function StatusDot({ status }) {
  const c = STATUS_COLOR[status] || 'var(--ink-3)'
  const live = status === 'THINKING' || status === 'ANALYSING'
  return (
    <span aria-hidden="true" style={{
      display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
      background: c, marginRight: 8, flex: 'none',
      boxShadow: `0 0 0 4px color-mix(in srgb, ${c} 18%, transparent)`,
      animation: live ? 'corePulse 1.6s ease-in-out infinite' : 'none',
    }} />
  )
}

export default function LiveCognition({ cognition, status, statusReason, compact }) {
  const steps = cognition?.steps || []

  return (
    <div className="bb-card" style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 10, marginBottom: 10 }}>
        <div className="bb-card-header" style={{ margin: 0 }}>Live reasoning</div>
        <span style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>
          {cognition?.cycle_count != null ? `Cycle ${cognition.cycle_count}` : ''}
        </span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', fontSize: 13.5, color: 'var(--ink-2)', marginBottom: 12 }}>
        <StatusDot status={status} />{statusReason}
      </div>

      {!steps.length && (
        <div style={{ fontSize: 13.5, color: 'var(--ink-3)', lineHeight: 1.6 }}>
          No reasoning cycle recorded yet. Each step of ARIA’s most recent
          pass appears here once it has actually run.
        </div>
      )}

      <div style={{ maxHeight: compact ? 300 : 460, overflowY: 'auto' }}>
        {steps.map((s, i) => {
          const c = PHASE_COLOR[s.phase] || 'var(--ink-3)'
          return (
            <div key={i} style={{
              display: 'grid', gridTemplateColumns: '10px 1fr', gap: 12,
              padding: '10px 0', borderTop: i ? '1px solid var(--line)' : 'none',
            }}>
              <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: '50%', background: c, marginTop: 6 }} />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>
                  {s.phase ? sentence(s.phase) : 'Step'}
                </div>
                <div style={{ fontSize: 13.5, color: 'var(--ink-2)', marginTop: 1 }}>
                  {s.activity}
                </div>
                {s.conclusion && (
                  <div style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 3, lineHeight: 1.5 }}>
                    → {s.conclusion}
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {!!(cognition?.decisions || []).length && (
        <div style={{ marginTop: 10, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
          <div style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--ink)', marginBottom: 6 }}>
            Decisions · {cognition.trades_queued || 0} queued for approval
          </div>
          {cognition.decisions.map((d, i) => (
            <div key={i} style={{ fontSize: 13.5, lineHeight: 1.6, color: 'var(--ink-2)' }}>
              <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{d.ticker}</span>
              {' · '}
              <span style={{
                color: d.action === 'PROPOSE_BUY' ? 'var(--green)'
                  : d.action === 'PROPOSE_SELL' ? 'var(--red)' : 'var(--ink-3)',
                fontWeight: 600,
              }}>{sentence(d.action)}</span>
              {d.reason ? ` · ${d.reason}` : ''}
            </div>
          ))}
        </div>
      )}

      <div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 12, lineHeight: 1.55 }}>
        {cognition?.note || 'Activity summaries, not internal reasoning.'}
        {' '}The brain proposes; the human approves.
      </div>
    </div>
  )
}
