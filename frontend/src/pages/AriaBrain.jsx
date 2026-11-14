/**
 * pages/AriaBrain.jsx — ARIA BRAIN. The hero, and the only intelligence.
 *
 * WHAT THIS REPLACED
 * ------------------
 * "ARIA", "Live Mind", "Cognitive Brain", a "Live Thought Stream", a "Memory
 * Browser" and a chat were six surfaces onto one daemon, one memory store and
 * one world model — and each looked, on screen, like a peer of the others. A
 * user could reasonably ask "is Live Mind thinking, or is the Brain?" and the
 * interface offered no way to answer.
 *
 * They are the same thing. So this page is that thing, and Live Mind no longer
 * exists as a destination, a tab, or a label.
 *
 * HIERARCHY
 * ---------
 * The layout encodes what matters, rather than giving every panel equal weight:
 *
 *   PRIMARY     ARIA BRAIN — status, regime, VIX, cycle, memory, model
 *   SECONDARY   the conversation, with all of the above already in its context
 *   TERTIARY    live cognition, memory, world context, news, today's report
 *
 * The regime appears as CONTEXT inside the header — it is a market condition,
 * not the subject of the page, and putting "Expansion (Goldilocks)" above ARIA
 * made a weather reading the hero.
 *
 * Everything reads from ONE endpoint, /api/brain, which composes the world
 * model, the regime classifier and the daemon. Panels no longer each fetch
 * their own slice and then disagree with each other on screen.
 */
import React, { useEffect, useState } from 'react'
import axios from 'axios'
import { Link } from 'react-router-dom'
import { useAlerts, useBrain, useBrainActivity, useDailyReport, useWorldChanges,
         searchMemory } from '../hooks/useApi'
import LiveCognition, { StatusDot, sentence } from '../components/LiveCognition'
import LiveNews from '../components/LiveNews'
import Chat from './Chat'
import { DataUnavailable, hasRecord, displayValue } from '../components/DataState'

const mono = { fontFamily: 'var(--font)' }

function ago(iso) {
  if (!iso) return 'never'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return `${Math.max(0, Math.round(s))}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

/* ── the header: ARIA BRAIN, and the context it is reasoning in ─────────── */

const Figure = ({ label, value, sub, color = 'var(--ink)', title, big }) => (
  <div style={{ minWidth: 0 }} title={title}>
    <div style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--ink-3)' }}>
      {label}
    </div>
    <div style={{
      fontFamily: 'var(--font-display)', fontSize: big ? 26 : 20, fontWeight: 650,
      letterSpacing: '-.02em', color, marginTop: 3, overflowWrap: 'anywhere',
    }}>
      {value ?? '—'}
    </div>
    {sub && <div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 2 }}>{sub}</div>}
  </div>
)

function BrainHeader({ brain }) {
  const status = brain?.status || 'IDLE'
  const regime = brain?.regime || {}
  const cycle = brain?.cycle || {}

  return (
    <>
      {/* PRIMARY — the identity. One name, one surface. */}
      <div style={{ margin: '4px 0 16px' }}>
        <h1 style={{ fontSize: 32, fontWeight: 700, letterSpacing: '-.03em' }}>Brain</h1>
        <div style={{ display: 'flex', alignItems: 'center', fontSize: 14.5, color: 'var(--ink-2)', marginTop: 4 }}>
          <StatusDot status={status} />
          <span>
            <strong style={{ color: 'var(--ink)', fontWeight: 600 }}>{sentence(status)}</strong>
            {' · '}
            {brain?.status_reason || 'One intelligence. Chat, memory, reasoning and vault knowledge are facets of it.'}
          </span>
        </div>
      </div>

      {/* SECONDARY — the context it is reasoning in. Regime lives HERE, as a
          market condition, not as the page's headline. */}
      <div className="bb-card" style={{ marginBottom: 16 }}>
        <div style={{
          display: 'grid', gap: '18px 26px',
          gridTemplateColumns: 'minmax(220px, 1.7fr) repeat(auto-fit, minmax(110px, 1fr))',
        }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--ink-3)' }}>
              Market regime
            </div>
            <div style={{
              fontFamily: 'var(--font-display)', fontSize: 26, fontWeight: 650,
              letterSpacing: '-.025em', color: 'var(--ink)', marginTop: 3,
            }}>
              {regime.label || '—'}
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 2 }}>
              {/* Coverage, not conviction. A regime carried by two of four
                  observed inputs is still the best available reading — it just
                  must not look like one carried by all four. */}
              {regime.confidence != null
                ? `${(regime.confidence * 100).toFixed(0)}% input coverage`
                : 'coverage unknown'}
              {regime.as_of ? ` · as of ${regime.as_of}` : ''}
              {regime.stale && (
                <span className="aria-pill warn" style={{ height: 22, fontSize: 12, marginLeft: 8 }}>
                  From stale data
                </span>
              )}
            </div>
          </div>
          <Figure big label="VIX" value={brain?.vix} />
          <Figure big label="Last cycle" value={cycle.last_at ? ago(cycle.last_at) : '—'}
            sub={cycle.count != null ? `${cycle.count} cycles` : null} />
          <Figure big label="Memories" value={brain?.memory?.count != null ? Number(brain.memory.count).toLocaleString() : '—'}
            sub={brain?.memory?.available ? 'attached' : 'not attached'} />
          <Figure label="Model" value={brain?.model || (brain?.llm_available ? 'ready' : 'offline')}
            color={brain?.llm_available ? 'var(--ink)' : 'var(--red)'}
            sub={<span style={{ color: brain?.llm_available ? 'var(--green)' : 'var(--red)' }}>
              {brain?.llm_available ? 'local model up' : 'local model unreachable'}</span>} />
        </div>

        {!!(regime.caveats || []).length && (
          <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--line)' }}>
            {regime.caveats.map((c, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.55, marginTop: i ? 6 : 0 }}>
                <span aria-hidden="true" style={{ color: 'var(--yellow)', flex: 'none' }}>△</span>{c}
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}

/* ── the brain's own controls, absorbed ─────────────────────────────────
   The daemon console was its own page. Merging a workspace must not quietly
   remove its controls, so every action it offered is here: start, pause, run a
   cycle now, set the interval, route hard steps to a frontier model, generate
   training data, and reindex the vault. What is gone is the second hero — not
   the buttons. */

const Btn = ({ children, onClick, disabled, color = 'var(--accent)' }) => (
  <button onClick={onClick} disabled={disabled} style={{
    fontSize: 13.5, fontWeight: 600, minHeight: 34,
    padding: '0 14px', borderRadius: 999, border: 'none',
    background: disabled ? 'var(--inset)' : `color-mix(in srgb, ${color} 13%, transparent)`,
    color: disabled ? 'var(--ink-3)' : color,
  }}>{children}</button>
)

function Controls({ brain, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [interval, setIntervalMin] = useState('')
  const [consult, setConsult] = useState(null)
  const [vault, setVault] = useState(null)

  const running = brain?.daemon?.running
  const thinking = brain?.daemon?.thinking
  const llm = brain?.llm_available

  useEffect(() => {
    axios.get('/api/brain/consult').then(r => setConsult(r.data)).catch(() => {})
    axios.get('/api/vault/status').then(r => setVault(r.data)).catch(() => {})
  }, [])

  const call = async (fn, ok) => {
    setBusy(true); setNote('')
    try { await fn(); setNote(ok); onChanged?.() }
    catch (e) { setNote(`⚠ ${e.response?.data?.detail || e.message}`) }
    finally { setBusy(false) }
  }

  return (
    <div className="bb-card" style={{ marginBottom: 16 }}>
      <div className="bb-card-header">Brain controls</div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        {running
          ? <Btn onClick={() => call(() => axios.post('/api/brain/stop'), 'Brain paused')}
              disabled={busy} color="var(--red)">⏸ Pause</Btn>
          : <Btn onClick={() => call(() => axios.post('/api/brain/start'), 'Brain started')}
              disabled={busy || !llm} color="var(--green)">▶ Start</Btn>}
        <Btn onClick={() => call(() => axios.post('/api/brain/run-now'), 'Cycle started')}
          disabled={busy || !llm || thinking}>▶ Run now</Btn>
        <Btn onClick={() => call(() => axios.post('/api/vault/reindex'), 'Reindexing vault…')}
          disabled={busy} color="var(--purple)">⟳ Reindex vault</Btn>
        <Btn onClick={() => call(() => axios.post('/api/brain/generate-training-data'),
          'Generating training data…')} disabled={busy}>⚙ Training data</Btn>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        <input value={interval} onChange={e => setIntervalMin(e.target.value)}
          placeholder="interval (min)" aria-label="Reasoning interval in minutes"
          style={{
            width: 130, fontSize: 13.5, minHeight: 34, padding: '0 12px', borderRadius: 999,
            background: 'var(--inset)', border: '1px solid var(--line)', color: 'var(--ink)',
          }} />
        <Btn disabled={busy || !interval}
          onClick={() => call(() => axios.post(
            `/api/brain/start?interval_minutes=${encodeURIComponent(interval)}`),
            `Interval set to ${interval}m`)}>Set</Btn>
        <Btn disabled={busy}
          color={consult?.enabled ? 'var(--yellow)' : 'var(--ink-3)'}
          onClick={() => call(async () => {
            const r = await axios.post(`/api/brain/consult?enabled=${!consult?.enabled}`)
            setConsult(c => ({ ...c, ...r.data }))
          }, consult?.enabled ? 'Frontier consult off' : 'Frontier consult on')}>
          ◆ Consult {consult?.enabled ? 'On' : 'Off'}
          {consult?.enabled && ` ${consult.calls_today}/${consult.daily_call_cap}`}
        </Btn>
      </div>

      <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.6 }}>
        {vault?.note || (vault?.documents != null
          ? `Vault: ${vault.documents} notes indexed` : 'Vault status unknown')}
        {!llm && <div style={{ color: 'var(--red)' }}>Local model server unreachable.</div>}
        {note && <div style={{ color: 'var(--yellow)', marginTop: 3 }}>{note}</div>}
      </div>
    </div>
  )
}


/* ── memory, absorbed ──────────────────────────────────────────────────── */

function Memory({ brain }) {
  const [q, setQ] = useState('')
  const [rows, setRows] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const run = async (e) => {
    e?.preventDefault()
    setBusy(true); setErr(null)
    try {
      const d = await searchMemory(q)
      setRows(d.memories || [])
      if (d.note) setErr(d.note)
    } catch (ex) { setErr(ex.message) } finally { setBusy(false) }
  }

  return (
    <div className="bb-card" style={{ marginBottom: 16 }}>
      <div className="bb-card-header" style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span>Memory</span>
        <span style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>
          {brain?.memory?.count != null ? `${Number(brain.memory.count).toLocaleString()} stored` : ''}
        </span>
      </div>
      <form onSubmit={run} style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
        <input value={q} onChange={e => setQ(e.target.value)}
          placeholder="Search what ARIA remembers…"
          aria-label="Search memory"
          className="aria-input" style={{ flex: 1, minHeight: 38, borderRadius: 999, fontSize: 14 }} />
        <button type="submit" disabled={busy} className="aria-btn" style={{ minHeight: 38 }}>{busy ? '…' : 'Recall'}</button>
      </form>
      {err && <div style={{ ...mono, fontSize: 12, color: 'var(--yellow)', marginBottom: 6 }}>{err}</div>}
      {rows && !rows.length && !err && (
        <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>
          Nothing recalled for that.
        </div>
      )}
      <div style={{ maxHeight: 260, overflowY: 'auto' }}>
        {(rows || []).map((m, i) => (
          <div key={i} style={{ padding: '5px 0', borderBottom: '1px solid var(--inset)' }}>
            <div style={{ ...mono, fontSize: 12, color: 'var(--ink)', lineHeight: 1.55 }}>
              {typeof m === 'string' ? m : (m.text || m.content || m.summary || JSON.stringify(m))}
            </div>
            {m?.metadata?.kind && (
              <div style={{ ...mono, fontSize: 11, color: 'var(--ink-3)', marginTop: 1 }}>
                {m.metadata.kind}{m.metadata.at ? ` · ${m.metadata.at}` : ''}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

/* ── world context ─────────────────────────────────────────────────────── */

function WorldContext({ brain, changes }) {
  const m = brain?.world?.market || {}
  const p = brain?.world?.portfolio || {}
  const rec = brain?.world?.record?.stats || {}
  const moved = changes?.changed || []

  return (
    <div className="bb-card" style={{ marginBottom: 16 }}>
      <div className="bb-card-header">Current world</div>
      <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap', marginBottom: 12 }}>
        <Figure label="Bullish" value={m.breadth?.bullish} color="var(--green)"
          sub={`${m.breadth?.advancing_pct ?? '—'}% advancing`} />
        <Figure label="Bearish" value={m.breadth?.bearish} color="var(--red)"
          sub={`of ${m.breadth?.tracked ?? '—'} tracked`} />
        <Figure label="Positions" value={p.open_positions} color="var(--accent)"
          sub={p.equity ? `equity ${Number(p.equity).toLocaleString()}` : null} />
        <Figure label="Graded" value={rec.resolved}
          sub={rec.hit_rate != null ? `${(rec.hit_rate * 100).toFixed(1)}% hit rate`
                                    : `${rec.pending ?? 0} pending`} />
      </div>
      <div style={{ ...mono, fontSize: 11, color: 'var(--ink-3)',
        marginBottom: 5 }}>
        What changed · 24h
      </div>
      {!moved.length && (
        <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>
          {changes?.note || 'Nothing in my worldview moved in the window.'}
        </div>
      )}
      {moved.map((c, i) => (
        <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'baseline', padding: '2px 0' }}>
          <span style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', minWidth: 118 }}>{c.field}</span>
          <span style={{ ...mono, fontSize: 12, color: 'var(--red)' }}>{displayValue(c.from)}</span>
          <span style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>→</span>
          <span style={{ ...mono, fontSize: 12, color: 'var(--green)', fontWeight: 600 }}>{displayValue(c.to)}</span>
        </div>
      ))}
      <Link to="/market" style={{ ...mono, fontSize: 12, color: 'var(--accent)',
        textDecoration: 'none', display: 'block', marginTop: 10, }}>
        Market workspace →
      </Link>
    </div>
  )
}

/* ── blind spots ───────────────────────────────────────────────────────── */

function BlindSpots({ list }) {
  if (!list?.length) return null
  return (
    <div className="bb-card" style={{ marginBottom: 16 }}>
      <div className="bb-card-header">
        What I cannot see right now
      </div>
      {list.map((u, i) => (
        <div key={i} style={{ ...mono, fontSize: 12, color: 'var(--ink-2)',
          lineHeight: 1.6, padding: '4px 0' }}>
          <span style={{ color: 'var(--yellow)', marginRight: 6 }}>△</span>{u}
        </div>
      ))}
    </div>
  )
}

/* ── alerts, absorbed ───────────────────────────────────────────────────
   Alerts were their own page rendering a four-column table with its own
   filter chips. They are not a destination: an alert is something ARIA
   noticed, which makes it part of what ARIA currently believes. Absorbed
   here, compact, severity-ordered. */

const SEV = { CRITICAL: 'var(--red)', WARNING: 'var(--yellow)', INFO: 'var(--ink-3)' }

function Alerts() {
  const { data } = useAlerts()
  const rows = Array.isArray(data?.alerts) ? data.alerts : []
  const order = { CRITICAL: 0, WARNING: 1, INFO: 2 }
  const sorted = [...rows].sort(
    (a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3))

  return (
    <div className="bb-card" style={{ marginBottom: 16 }}>
      <div className="bb-card-header" style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span>Alerts</span>
        <span style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>
          {data?.critical ? `${data.critical} Critical · ` : ''}{data?.count ?? 0} total
        </span>
      </div>
      {!sorted.length && (
        <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>
          Nothing flagged.
        </div>
      )}
      <div style={{ maxHeight: 220, overflowY: 'auto' }}>
        {sorted.slice(0, 20).map((a, i) => (
          <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid var(--inset)' }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
              <span style={{
                ...mono, fontSize: 11, fontWeight: 600, 
                color: SEV[a.severity] || 'var(--ink-3)', minWidth: 52,
              }}>{a.severity}</span>
              <span style={{ ...mono, fontSize: 12, fontWeight: 600, color: 'var(--ink)',
                minWidth: 54 }}>{a.ticker || 'Market'}</span>
              <span style={{ ...mono, fontSize: 12, color: 'var(--ink)', flex: 1 }}>
                {a.title}
              </span>
            </div>
            {a.message && (
              <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)',
                marginTop: 1, paddingLeft: 60 }}>
                {String(a.message).slice(0, 120)}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}


/* ── today's report — a SUMMARY and a link, never the report itself ─────── */

function Today({ report }) {
  const missing = report?.status === 'NOT_GENERATED'
  return (
    <div className="bb-card" style={{ marginBottom: 16 }}>
      <div className="bb-card-header" style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span>Today</span>
        <span style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>{report?.date}</span>
      </div>
      {missing ? (
        <div style={{ ...mono, fontSize: 12, color: 'var(--yellow)', lineHeight: 1.7 }}>
          REPORT NOT GENERATED for {report.date}.
          <div style={{ color: 'var(--ink-3)', marginTop: 4 }}>
            {report.note}
            {report.most_recent_available &&
              ` Most recent available: ${report.most_recent_available}.`}
          </div>
        </div>
      ) : (
        <div style={{ ...mono, fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7 }}>
          {report?.executive_summary || '—'}
        </div>
      )}
      <Link to="/daily-report" style={{ ...mono, fontSize: 12, color: 'var(--accent)',
        textDecoration: 'none', display: 'block', marginTop: 9, }}>
        Daily report →
      </Link>
    </div>
  )
}

/* ── the page ──────────────────────────────────────────────────────────── */

export default function AriaBrain() {
  const { data: brain, error, loading, reload: reloadBrain } = useBrain()
  const { data: activity } = useBrainActivity(60)
  const { data: changes } = useWorldChanges(24)
  const { data: report } = useDailyReport()

  if (!loading && (error || !hasRecord(brain) || !brain.status)) return <DataUnavailable name="ARIA Brain" error={error} />

  return (
    <div>
      <BrainHeader brain={brain} />

      <div className="aria-two">
        <div style={{ minWidth: 0 }}>
          {/* SECONDARY — the conversation. The world model, regime, signals,
              memory and vault are already in its context server-side, so
              "why are you bullish on X?" is answerable without the user first
              going somewhere else to look. */}
          <Chat embedded />

          <div style={{ marginTop: 16 }}>
            <LiveNews compact limit={12} hours={24} />
          </div>
        </div>

        <div style={{ minWidth: 0 }}>
          <LiveCognition
            cognition={activity?.cognition || brain?.cognition}
            status={activity?.status || brain?.status}
            statusReason={activity?.status_reason || brain?.status_reason}
            compact
          />
          <WorldContext brain={brain} changes={changes} />
          <Memory brain={brain} />
          <Controls brain={brain} onChanged={reloadBrain} />
          <Today report={report} />
          <Alerts />
          <BlindSpots list={brain?.world?.unknowns} />
        </div>
      </div>
    </div>
  )
}
