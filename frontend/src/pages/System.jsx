/**
 * pages/System.jsx — §30, self-monitoring made visible.
 *
 * Every daemon in ARIA catches its own exceptions and keeps going. That is
 * right for uptime and blinding for observability: before the worker registry
 * existed, a loop that died in July looked identical to one that ran a second
 * ago, and the V5 prediction leg really did stop for a week without anything
 * anywhere turning red.
 *
 * This page is the answer to "is ARIA actually working?" — and it is allowed
 * to say no. A green board that cannot go red is decoration.
 */
import React from 'react'
import { useWorkers, useWorld, useConsultStatus } from '../hooks/useApi'
import { Empty, SectionHeader, Spinner } from '../components/UI'

const mono = { fontFamily: 'var(--mono)' }

/* SENTINEL is a SEPARATE general intelligence in its own process — not an ARIA
 * subsystem, and deliberately not drawn like one. Every state below is either
 * AVAILABLE or a reason it is not; none of them is "agrees". An unreachable
 * consultant that rendered as a quiet green dot would be the interface telling
 * the same lie the backend is built to refuse. */
const CONSULT_STATE = {
  AVAILABLE:     ['var(--green)',  'AVAILABLE'],
  UNAVAILABLE:   ['var(--muted)',  'UNAVAILABLE'],
  TIMEOUT:       ['var(--yellow)', 'TIMEOUT'],
  MALFORMED:     ['var(--yellow)', 'MALFORMED'],
  UNAUTHORISED:  ['var(--red)',    'UNAUTHORISED'],
  NO_TOKEN:      ['var(--muted)',  'NO TOKEN'],
  ERROR:         ['var(--red)',    'ERROR'],
  NOT_CONSULTED: ['var(--muted)',  'NOT CONSULTED'],
}

function SentinelPanel() {
  const { data } = useConsultStatus()
  if (!data) return null
  return <SentinelPanelView data={data} />
}

/* Split from the fetch above so every state it can render — including the ones
 * that need a consultant to be broken in a specific way — can be looked at
 * without arranging for SENTINEL to actually be broken that way. */
export function SentinelPanelView({ data }) {
  const state = data.sentinel_status || 'UNAVAILABLE'
  const [color, label] = CONSULT_STATE[state] || CONSULT_STATE.UNAVAILABLE
  const live = state === 'AVAILABLE'

  return (
    <div className="bb-card" style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', gap: 14, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <div style={{ ...mono, fontSize: 12, fontWeight: 800, letterSpacing: '.1em',
                      color: 'var(--orange)' }}>
          ◇ SENTINEL
        </div>
        <div style={{ ...mono, fontSize: 9, color: 'var(--muted)' }}>
          external advisory intelligence · separate system
        </div>
        <div style={{ ...mono, fontSize: 12, fontWeight: 800, color, marginLeft: 'auto',
                      letterSpacing: '.1em' }}>
          {label}
        </div>
      </div>

      <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 6 }}>
        {data.url}
        {' · '}auto-consult on the trading path is{' '}
        <span style={{ color: data.auto_consult_enabled ? 'var(--green)' : 'var(--muted)' }}>
          {data.auto_consult_enabled ? 'ON' : 'OFF'}
        </span>
        {' '}({data.auto_consult_flag})
      </div>

      {/* The whole point of the panel. When there is no consultant, say so in
        * the words that stop a reader filling in the blank themselves. */}
      {!live && (
        <div style={{ marginTop: 9, borderTop: '1px solid #141414', paddingTop: 8 }}>
          <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
            ARIA is operating independently.{' '}
            <span style={{ color: 'var(--yellow)' }}>Silence is not agreement.</span>
          </div>
          {data.blocked_by && (
            <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 3 }}>
              {data.blocked_by}
            </div>
          )}
        </div>
      )}

      {live && (
        <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 8,
                      borderTop: '1px solid #141414', paddingTop: 8 }}>
          SENTINEL advises. ARIA decides. A consultation cannot approve, block,
          or execute a trade — objections are attached to the proposal for the
          human approving it to read.
        </div>
      )}
    </div>
  )
}

const STATE_STYLE = {
  ok: ['var(--green)', '●', 'running on schedule'],
  late: ['var(--yellow)', '◐', 'overdue, not yet alarming'],
  stalled: ['var(--red)', '○', 'has not reported in over 3× its interval'],
  failing: ['var(--red)', '✕', 'last run raised'],
  unknown: ['var(--muted)', '?', 'registered but has never reported'],
  disabled: ['var(--muted)', '—', 'switched off deliberately'],
}

function age(sec) {
  if (sec == null) return 'never'
  if (sec < 90) return `${Math.round(sec)}s ago`
  if (sec < 5400) return `${Math.round(sec / 60)}m ago`
  if (sec < 172800) return `${Math.round(sec / 3600)}h ago`
  return `${Math.round(sec / 86400)}d ago`
}

const LOOP_NOTE = {
  fast: 'Seconds to minutes — monitoring, exits, anomaly detection.',
  medium: 'Minutes to hours — research, reasoning, portfolio analysis.',
  slow: 'Daily to weekly — strategy evaluation, retraining, grading.',
}

function WorkerRow({ w }) {
  const [color, glyph, meaning] = STATE_STYLE[w.state] || STATE_STYLE.unknown
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 11, padding: '8px 0',
                  borderBottom: '1px solid #141414' }}>
      <span style={{ color, fontSize: 12, width: 12, textAlign: 'center', marginTop: 1 }}>{glyph}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ ...mono, fontSize: 11, fontWeight: 700, color: '#fff' }}>
          {w.label}
          <span style={{ color: 'var(--muted)', fontWeight: 400, marginLeft: 8 }}>{w.name}</span>
        </div>
        <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 2 }}>
          {meaning} · ran {age(w.age_seconds)} · {w.ticks} ticks
          {w.failures > 0 && <span style={{ color: 'var(--yellow)' }}> · {w.failures} failures</span>}
          {w.next_expected && <span> · next ~{new Date(w.next_expected).toLocaleTimeString()}</span>}
        </div>
        {w.last_error && (w.state === 'failing' || w.state === 'disabled') && (
          <div style={{ ...mono, fontSize: 9, color: 'var(--red)', marginTop: 3,
                        wordBreak: 'break-word' }}>
            {w.last_error}
          </div>
        )}
      </div>
      <span style={{ ...mono, fontSize: 9, letterSpacing: '.1em', color,
                     textTransform: 'uppercase' }}>{w.state}</span>
    </div>
  )
}

export default function System() {
  const { data, loading } = useWorkers()
  const { data: world } = useWorld()

  if (loading && !data) return <Spinner />

  const workers = data?.workers || []
  const counts = data?.counts || {}
  const byLoop = ['fast', 'medium', 'slow'].map(l => [l, workers.filter(w => w.loop === l)])
  const senses = world?.system?.senses?.senses || []

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto' }}>
      <div style={{ marginBottom: 12 }}>
        <div style={{ ...mono, fontSize: 16, fontWeight: 800, color: 'var(--orange)', letterSpacing: '.12em' }}>
          ⚙ SYSTEM
        </div>
        <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
          What is running, when it last ran, and what broke.
        </div>
      </div>

      <SentinelPanel />

      {/* The headline is allowed to be bad news. */}
      <div className="bb-card" style={{
        marginBottom: 14,
        borderColor: data?.healthy === false ? 'var(--red)' : 'var(--border)',
      }}>
        <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap', alignItems: 'center' }}>
          <div style={{ ...mono, fontSize: 15, fontWeight: 800,
                        color: data?.healthy === false ? 'var(--red)' : 'var(--green)' }}>
            {data?.healthy === false ? '✕ DEGRADED' : '● ALL WORKERS REPORTING'}
          </div>
          {Object.entries(counts).filter(([, n]) => n > 0).map(([k, n]) => (
            <div key={k} style={{ ...mono, fontSize: 10, color: (STATE_STYLE[k] || [])[0] || 'var(--muted)' }}>
              {n} {k}
            </div>
          ))}
          <div style={{ ...mono, fontSize: 10, color: 'var(--muted)', marginLeft: 'auto' }}>
            {world?.system?.activity_24h?.total ?? 0} events in 24h
          </div>
        </div>
        {!!(data?.degraded || []).length && (
          <div style={{ marginTop: 10, borderTop: '1px solid #141414', paddingTop: 8 }}>
            {data.degraded.map(d => (
              <div key={d.name} style={{ ...mono, fontSize: 10, color: 'var(--red)', padding: '2px 0' }}>
                {d.label} is {d.state}
                {d.last_error ? ` — ${d.last_error}` : ''}
                {' '}· anything downstream of it is not updating.
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── the three cadences (§19) ── */}
      {byLoop.map(([loop, rows]) => (
        <div key={loop} style={{ marginBottom: 14 }}>
          <SectionHeader>{loop.toUpperCase()} LOOP</SectionHeader>
          <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginBottom: 6 }}>
            {LOOP_NOTE[loop]}
          </div>
          <div className="bb-card">
            {!rows.length && <Empty message="No workers registered in this loop." />}
            {rows.map(w => <WorkerRow key={w.name} w={w} />)}
          </div>
        </div>
      ))}

      {/* ── data freshness ── */}
      <SectionHeader>DATA FRESHNESS</SectionHeader>
      <div className="bb-card" style={{ marginBottom: 14 }}>
        {!world?.unknowns?.length && (
          <div style={{ ...mono, fontSize: 10, color: 'var(--green)' }}>
            Every source ARIA reads is inside its freshness window.
          </div>
        )}
        {(world?.unknowns || []).map((u, i) => (
          <div key={i} style={{ ...mono, fontSize: 10, color: 'var(--text-dim)',
                                lineHeight: 1.65, padding: '4px 0' }}>
            <span style={{ color: 'var(--yellow)', marginRight: 7 }}>△</span>{u}
          </div>
        ))}
      </div>

      {/* ── the four senses (src/brain/self_state.py, kept and surfaced) ── */}
      {!!senses.length && (
        <>
          <SectionHeader>SELF-CHECKS</SectionHeader>
          <div className="bb-card">
            {senses.map(s => (
              <div key={s.name} style={{ display: 'flex', gap: 11, padding: '5px 0',
                                         borderBottom: '1px solid #141414' }}>
                <span style={{ ...mono, fontSize: 10, fontWeight: 700, minWidth: 62,
                               color: s.state === 'ok' ? 'var(--green)'
                                    : s.state === 'unknown' ? 'var(--muted)' : 'var(--yellow)' }}>
                  {s.name.toUpperCase()}
                </span>
                <span style={{ ...mono, fontSize: 10, color: 'var(--text-dim)', flex: 1 }}>
                  {s.detail}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
