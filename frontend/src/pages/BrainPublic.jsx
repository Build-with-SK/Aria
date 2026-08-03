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

const mono = { fontFamily: 'var(--mono)' }

// The daemon's own cycle. Shown as a ring of labels so the live step reads as
// a position in a loop rather than a status string.
const STEPS = ['ORIENT', 'FOCUS', 'RECALL', 'ANALYSE', 'DECIDE', 'REFLECT']

function Stat({ label, value, live }) {
  return (
    <div style={{
      background: '#0b080c', border: '1px solid var(--border)', borderRadius: 4,
      padding: '10px 12px', minWidth: 0,
    }}>
      <div style={{ ...mono, fontSize: 8.5, color: 'var(--muted)', letterSpacing: '0.16em' }}>
        {label}
      </div>
      <div style={{
        ...mono, fontSize: 17, fontWeight: 700, marginTop: 4,
        color: live ? 'var(--green)' : '#e0e0e0',
        textShadow: live ? '0 0 10px rgba(43,227,139,.4)' : 'none',
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
        <h1 style={{ ...mono, fontSize: 16, fontWeight: 700, color: 'var(--orange)',
                     letterSpacing: '0.14em', margin: 0 }}>
          ◈ THE COGNITIVE ENGINE
        </h1>
        <p style={{ ...mono, fontSize: 11, color: 'var(--muted)', lineHeight: 1.7, margin: '8px 0 0', maxWidth: 720 }}>
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
        <Stat label="STATE" value={err ? 'OFFLINE' : running ? 'ALIVE' : 'IDLE'} live={running && !err} />
        <Stat label="CYCLES" value={d.cycle_count ?? '—'} />
        <Stat label="MEMORIES" value={d.memory_count ?? '—'} />
        <Stat label="MODEL" value={d.model || '—'} />
      </div>

      {/* The loop, with the live step lit. */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 18 }}>
        {STEPS.map(s => {
          const on = step === s
          return (
            <div key={s} style={{
              ...mono, fontSize: 9, letterSpacing: '0.14em', padding: '5px 10px',
              borderRadius: 3,
              border: `1px solid ${on ? 'var(--orange)' : 'var(--border)'}`,
              background: on ? 'rgba(255,36,71,0.10)' : 'transparent',
              color: on ? 'var(--orange)' : 'var(--muted)',
            }}>
              {s}
            </div>
          )
        })}
        {d.thinking && (
          <div style={{ ...mono, fontSize: 9, letterSpacing: '0.14em', padding: '5px 10px',
                        color: 'var(--green)' }}>
            ● THINKING
          </div>
        )}
      </div>

      <LivingCore height={520} />

      <p style={{ ...mono, fontSize: 9.5, color: 'var(--muted)', lineHeight: 1.7, marginTop: 14 }}>
        Every body in the field is a real instrument from the tracked universe,
        placed by region and lit by conviction. Research and education only —
        ARIA is not a licensed adviser.
      </p>
    </div>
  )
}
