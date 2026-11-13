/**
 * pages/BrainPublic.jsx
 * The brain as everyone else sees it.
 *
 * Watching ARIA think is the most compelling thing this system does, so it
 * should not sit behind an owner check. What it must not do is show WHAT she is
 * thinking about: the reasoning loop runs with the owner's Obsidian vault in
 * context, so cycle transcripts and stored memories can quote personal notes.
 *
 * So this renders the galaxy and the vital signs — alive, which step, how many
 * cycles, how many memories — from /api/brain/pulse, the one brain endpoint
 * that carries telemetry and no prose. Everything textual, and every control,
 * stays with the owner.
 */
import React, { useEffect, useState } from 'react'
import axios from 'axios'
import LivingCore from '../components/LivingCore'

const mono = { fontFamily: 'var(--font)' }

/* The six phases of one reasoning cycle, in order — the real names the
   reasoner records against each thought (src/brain/cognitive/reasoner.py).

   These are INDICATORS, not controls. They were first drawn as bordered chips,
   which reads as a row of buttons, so they looked broken rather than
   informative — nothing happens when you click, because nothing should. They
   are now a progress track: the completed phases stay lit, the live one pulses,
   and the ones still to come are dim. */
const STEPS = [
  ['ORIENT', 'takes in the state of the market'],
  ['FOCUS', 'picks what is worth thinking about'],
  ['RECALL', 'pulls up what it learned before'],
  ['ANALYSE', 'works through the evidence'],
  ['DECIDE', 'commits to a view'],
  ['REFLECT', 'checks itself, and remembers'],
]

function Stat({ label, value, live }) {
  return (
    <div style={{
      background: 'var(--inset)', border: '1px solid var(--line)', borderRadius: 10,
      padding: '10px 12px', minWidth: 0,
    }}>
      <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', }}>
        {label}
      </div>
      <div style={{
        ...mono, fontSize: 18, fontWeight: 600, marginTop: 4,
        color: live ? 'var(--green)' : 'var(--ink)',
      }}>
        {value}
      </div>
    </div>
  )
}

export default function BrainPublic() {
  const [pulse, setPulse] = useState(null)
  const [err, setErr] = useState(false)

  useEffect(() => {
    let alive = true
    const tick = () => {
      axios.get('/api/brain/pulse')
        .then(r => { if (alive) { setPulse(r.data); setErr(false) } })
        .catch(() => { if (alive) setErr(true) })
    }
    tick()
    const id = setInterval(tick, 4000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  const d = pulse?.daemon || {}
  const running = !!pulse?.alive
  const step = (d.step || '').toUpperCase()

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ ...mono, fontSize: 17, fontWeight: 600, color: 'var(--accent)',
                     margin: 0 }}>
          ◈ The cognitive engine
        </h1>
        <p style={{ ...mono, fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.7, margin: '8px 0 0', maxWidth: 720 }}>
          ARIA runs a continuous reasoning loop on a local model — orient, focus,
          recall, analyse, decide, reflect — and writes what she learns to a
          long-term memory she can search later. This is her heartbeat, live.
          What she is reasoning <em>about</em> is private to her owner.
        </p>
      </div>

      <div style={{
        display: 'grid', gap: 10, marginBottom: 16,
        gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
      }}>
        <Stat label="State" value={err ? 'OFFLINE' : running ? 'ALIVE' : 'IDLE'} live={running && !err} />
        <Stat label="Cycles" value={d.cycle_count ?? '—'} />
        <Stat label="Memories" value={d.memory_count ?? '—'} />
        <Stat label="Model" value={d.model || '—'} />
      </div>

      {/* One cycle, as a progress track. Read-only: these report, they do not
          control. `aria-current` marks the live phase for screen readers. */}
      <div role="list" aria-label="Reasoning cycle" style={{ marginBottom: 20 }}>
        <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)',
                      marginBottom: 8 }}>
          One cycle of thought{d.thinking ? ' · Running now' :
            step ? ' · Last completed' : ''}
        </div>
        <div style={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
          {STEPS.map(([name, what], i) => {
            const at = STEPS.findIndex(([n]) => n === step)
            const live = step === name && d.thinking
            const done = at >= 0 && i <= at
            const colour = live ? 'var(--accent)' : done ? 'var(--green)' : 'var(--ink-3)'
            return (
              <div key={name} role="listitem" title={`${name} — ${what}`}
                aria-current={live ? 'step' : undefined}
                style={{ flex: '1 1 120px', minWidth: 0 }}>
                <div style={{
                  height: 2, borderRadius: 8, marginBottom: 6,
                  background: live || done ? colour : 'var(--inset)',
                  animation: live ? 'corePulse 1.6s ease-in-out infinite' : 'none',
                }} />
                <div style={{ ...mono, fontSize: 12, color: colour }}>
                  {name}
                </div>
                <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)',
                              lineHeight: 1.5, marginTop: 2 }}>
                  {what}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      <LivingCore height={520} />

      <p style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.7, marginTop: 14 }}>
        Every body in the field is a real instrument from the tracked universe,
        placed by region and lit by conviction. Research and education only —
        ARIA is not a licensed adviser.
      </p>
    </div>
  )
}
