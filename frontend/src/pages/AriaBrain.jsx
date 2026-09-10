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
import LiveCognition, { StatusDot } from '../components/LiveCognition'
import LiveNews from '../components/LiveNews'
import Chat from './Chat'

const mono = { fontFamily: 'var(--mono)' }

function ago(iso) {
  if (!iso) return 'never'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return `${Math.max(0, Math.round(s))}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

/* ── the header: ARIA BRAIN, and the context it is reasoning in ─────────── */

const Figure = ({ label, value, sub, color = '#fff', title }) => (
  <div style={{ minWidth: 78 }} title={title}>
    <div style={{ ...mono, fontSize: 8, letterSpacing: '.2em', color: 'var(--muted)' }}>
      {label}
    </div>
    <div style={{ ...mono, fontSize: 17, fontWeight: 800, color, marginTop: 2 }}>
      {value ?? '—'}
    </div>
    {sub && <div style={{ ...mono, fontSize: 8, color: 'var(--muted)', marginTop: 1 }}>{sub}</div>}
  </div>
)

function BrainHeader({ brain }) {
  const status = brain?.status || 'IDLE'
  const regime = brain?.regime || {}
  const cycle = brain?.cycle || {}

  return (
    <div className="glow-frame" style={{
      background: 'radial-gradient(720px 260px at 12% 0%, rgba(255,36,71,.055), transparent), var(--bg-1)',
      padding: '20px 24px', marginBottom: 16,
    }}>
      {/* PRIMARY — the identity. One name, one surface. */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap' }}>
        <div style={{
          ...mono, fontSize: 24, fontWeight: 800, color: '#fff', letterSpacing: '.14em',
        }}>
          ARIA
        </div>
        <div style={{
          ...mono, fontSize: 11, fontWeight: 700, color: 'var(--orange)',
          letterSpacing: '.32em',
        }}>
          COGNITIVE BRAIN
        </div>
        <div style={{ ...mono, fontSize: 10, marginLeft: 'auto', color: 'var(--text-dim)' }}>
          <StatusDot status={status} />
          <strong style={{ letterSpacing: '.12em' }}>{status}</strong>
        </div>
      </div>
      <div style={{ ...mono, fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
        {brain?.status_reason || 'One intelligence. Chat, memory, reasoning and vault knowledge are facets of it.'}
      </div>

      {/* SECONDARY — the context it is reasoning in. Regime lives HERE, as a
          market condition, not as the page's headline. */}
      <div style={{
        display: 'flex', gap: 26, flexWrap: 'wrap', marginTop: 18,
        paddingTop: 15, borderTop: '1px solid var(--border)',
      }}>
        <div style={{ minWidth: 240 }}>
          <div style={{ ...mono, fontSize: 8, letterSpacing: '.2em', color: 'var(--muted)' }}>
            CURRENT REGIME
          </div>
          <div style={{ ...mono, fontSize: 15, fontWeight: 800, color: 'var(--orange)', marginTop: 2 }}>
            {regime.label || '—'}
            {regime.stale && (
              <span style={{ ...mono, fontSize: 9, color: 'var(--yellow)', marginLeft: 8 }}>
                ⚠ FROM STALE DATA
              </span>
            )}
          </div>
          <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 2 }}>
            {/* Coverage, not conviction. A regime carried by two of four
                observed inputs is still the best available reading — it just
                must not look like one carried by all four. */}
            {regime.confidence != null
              ? `${(regime.confidence * 100).toFixed(0)}% input coverage`
              : 'coverage unknown'}
            {regime.as_of ? ` · as of ${regime.as_of}` : ''}
          </div>
        </div>
        <Figure label="VIX" value={brain?.vix} color="var(--blue)" />
        <Figure label="LAST CYCLE" value={cycle.last_at ? ago(cycle.last_at) : '—'}
          sub={cycle.count != null ? `${cycle.count} cycles` : null} />
        <Figure label="MEMORY" value={brain?.memory?.count ?? '—'}
          sub={brain?.memory?.available ? 'attached' : 'not attached'} />
        <Figure label="MODEL" value={brain?.model || (brain?.llm_available ? 'ready' : 'offline')}
          color={brain?.llm_available ? 'var(--green)' : 'var(--red)'}
          sub={brain?.llm_available ? 'local model up' : 'local model unreachable'} />
      </div>

      {!!(regime.caveats || []).length && (
        <div style={{ marginTop: 12 }}>
          {regime.caveats.map((c, i) => (
            <div key={i} style={{ ...mono, fontSize: 9, color: 'var(--yellow)', lineHeight: 1.6 }}>
              △ {c}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/* ── the brain's own controls, absorbed ─────────────────────────────────
   The daemon console was its own page. Merging a workspace must not quietly
   remove its controls, so every action it offered is here: start, pause, run a
   cycle now, set the interval, route hard steps to a frontier model, generate
   training data, and reindex the vault. What is gone is the second hero — not
   the buttons. */

const Btn = ({ children, onClick, disabled, color = 'var(--orange)' }) => (
  <button onClick={onClick} disabled={disabled} style={{
    ...mono, fontSize: 9, fontWeight: 800, letterSpacing: '.1em',
    padding: '5px 10px', borderRadius: 3,
    cursor: disabled ? 'not-allowed' : 'pointer',
    background: 'transparent', border: `1px solid ${disabled ? '#2a2a2a' : color}`,
    color: disabled ? '#555' : color,
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
    <div className="bb-card" style={{ marginBottom: 14 }}>
      <div className="bb-card-header">BRAIN CONTROLS</div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
        {running
          ? <Btn onClick={() => call(() => axios.post('/api/brain/stop'), 'Brain paused')}
              disabled={busy} color="var(--red)">⏸ PAUSE</Btn>
          : <Btn onClick={() => call(() => axios.post('/api/brain/start'), 'Brain started')}
              disabled={busy || !llm} color="var(--green)">▶ START</Btn>}
        <Btn onClick={() => call(() => axios.post('/api/brain/run-now'), 'Cycle started')}
          disabled={busy || !llm || thinking}>▶ RUN NOW</Btn>
        <Btn onClick={() => call(() => axios.post('/api/vault/reindex'), 'Reindexing vault…')}
          disabled={busy} color="#b366ff">⟳ REINDEX VAULT</Btn>
        <Btn onClick={() => call(() => axios.post('/api/brain/generate-training-data'),
          'Generating training data…')} disabled={busy}>⚙ TRAINING DATA</Btn>
      </div>

      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 8 }}>
        <input value={interval} onChange={e => setIntervalMin(e.target.value)}
          placeholder="interval (min)" aria-label="Reasoning interval in minutes"
          style={{
            ...mono, width: 110, fontSize: 9, padding: '5px 7px', borderRadius: 3,
            background: '#0c0a0c', border: '1px solid var(--border)', color: 'var(--text)',
          }} />
        <Btn disabled={busy || !interval}
          onClick={() => call(() => axios.post(
            `/api/brain/start?interval_minutes=${encodeURIComponent(interval)}`),
            `Interval set to ${interval}m`)}>SET</Btn>
        <Btn disabled={busy}
          color={consult?.enabled ? '#fbbf24' : 'var(--muted)'}
          onClick={() => call(async () => {
            const r = await axios.post(`/api/brain/consult?enabled=${!consult?.enabled}`)
            setConsult(c => ({ ...c, ...r.data }))
          }, consult?.enabled ? 'Frontier consult off' : 'Frontier consult on')}>
          ◆ CONSULT {consult?.enabled ? 'ON' : 'OFF'}
          {consult?.enabled && ` ${consult.calls_today}/${consult.daily_call_cap}`}
        </Btn>
      </div>

      <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', lineHeight: 1.6 }}>
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
    <div className="bb-card" style={{ marginBottom: 14 }}>
      <div className="bb-card-header" style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span>MEMORY</span>
        <span style={{ ...mono, fontSize: 9, color: 'var(--muted)' }}>
          {brain?.memory?.count != null ? `${brain.memory.count} STORED` : ''}
        </span>
      </div>
      <form onSubmit={run} style={{ display: 'flex', gap: 6, marginBottom: 9 }}>
        <input value={q} onChange={e => setQ(e.target.value)}
          placeholder="Search what ARIA remembers…"
          aria-label="Search memory"
          style={{
            ...mono, flex: 1, fontSize: 10, padding: '6px 8px', borderRadius: 3,
            background: '#0c0a0c', border: '1px solid var(--border)', color: 'var(--text)',
          }} />
        <button type="submit" disabled={busy} style={{
          ...mono, fontSize: 9, fontWeight: 800, letterSpacing: '.1em',
          padding: '6px 12px', borderRadius: 3, cursor: busy ? 'wait' : 'pointer',
          background: 'transparent', border: '1px solid var(--orange)', color: 'var(--orange)',
        }}>{busy ? '…' : 'RECALL'}</button>
      </form>
      {err && <div style={{ ...mono, fontSize: 9, color: 'var(--yellow)', marginBottom: 6 }}>{err}</div>}
      {rows && !rows.length && !err && (
        <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
          Nothing recalled for that.
        </div>
      )}
      <div style={{ maxHeight: 260, overflowY: 'auto' }}>
        {(rows || []).map((m, i) => (
          <div key={i} style={{ padding: '5px 0', borderBottom: '1px solid #121212' }}>
            <div style={{ ...mono, fontSize: 10, color: 'var(--text)', lineHeight: 1.55 }}>
              {typeof m === 'string' ? m : (m.text || m.content || m.summary || JSON.stringify(m))}
            </div>
            {m?.metadata?.kind && (
              <div style={{ ...mono, fontSize: 8, color: 'var(--muted)', marginTop: 1 }}>
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
    <div className="bb-card" style={{ marginBottom: 14 }}>
      <div className="bb-card-header">CURRENT WORLD</div>
      <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap', marginBottom: 12 }}>
        <Figure label="BULLISH" value={m.breadth?.bullish} color="var(--green)"
          sub={`${m.breadth?.advancing_pct ?? '—'}% advancing`} />
        <Figure label="BEARISH" value={m.breadth?.bearish} color="var(--red)"
          sub={`of ${m.breadth?.tracked ?? '—'} tracked`} />
        <Figure label="POSITIONS" value={p.open_positions} color="var(--orange)"
          sub={p.equity ? `equity ${Number(p.equity).toLocaleString()}` : null} />
        <Figure label="GRADED" value={rec.resolved}
          sub={rec.hit_rate != null ? `${(rec.hit_rate * 100).toFixed(1)}% hit rate`
                                    : `${rec.pending ?? 0} pending`} />
      </div>
      <div style={{ ...mono, fontSize: 8, letterSpacing: '.2em', color: 'var(--muted)',
        marginBottom: 5 }}>
        WHAT CHANGED · 24H
      </div>
      {!moved.length && (
        <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
          {changes?.note || 'Nothing in my worldview moved in the window.'}
        </div>
      )}
      {moved.map((c, i) => (
        <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'baseline', padding: '2px 0' }}>
          <span style={{ ...mono, fontSize: 9, color: 'var(--muted)', minWidth: 118 }}>{c.field}</span>
          <span style={{ ...mono, fontSize: 10, color: 'var(--red)' }}>{String(c.from)}</span>
          <span style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>→</span>
          <span style={{ ...mono, fontSize: 10, color: 'var(--green)', fontWeight: 700 }}>{String(c.to)}</span>
        </div>
      ))}
      <Link to="/market" style={{ ...mono, fontSize: 9, color: 'var(--orange)',
        textDecoration: 'none', display: 'block', marginTop: 10, letterSpacing: '.14em' }}>
        MARKET WORKSPACE →
      </Link>
    </div>
  )
}

/* ── blind spots ───────────────────────────────────────────────────────── */

function BlindSpots({ list }) {
  if (!list?.length) return null
  return (
    <div className="bb-card" style={{ marginBottom: 14, borderColor: 'rgba(255,179,36,.3)' }}>
      <div className="bb-card-header" style={{ color: 'var(--yellow)' }}>
        WHAT I CANNOT SEE RIGHT NOW
      </div>
      {list.map((u, i) => (
        <div key={i} style={{ ...mono, fontSize: 10, color: 'var(--text-dim)',
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

const SEV = { CRITICAL: 'var(--red)', WARNING: 'var(--yellow)', INFO: 'var(--muted)' }

function Alerts() {
  const { data } = useAlerts()
  const rows = Array.isArray(data?.alerts) ? data.alerts : []
  const order = { CRITICAL: 0, WARNING: 1, INFO: 2 }
  const sorted = [...rows].sort(
    (a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3))

  return (
    <div className="bb-card" style={{ marginBottom: 14 }}>
      <div className="bb-card-header" style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span>ALERTS</span>
        <span style={{ ...mono, fontSize: 9, color: 'var(--muted)' }}>
          {data?.critical ? `${data.critical} CRITICAL · ` : ''}{data?.count ?? 0} TOTAL
        </span>
      </div>
      {!sorted.length && (
        <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
          Nothing flagged.
        </div>
      )}
      <div style={{ maxHeight: 220, overflowY: 'auto' }}>
        {sorted.slice(0, 20).map((a, i) => (
          <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid #121212' }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
              <span style={{
                ...mono, fontSize: 8, fontWeight: 800, letterSpacing: '.1em',
                color: SEV[a.severity] || 'var(--muted)', minWidth: 52,
              }}>{a.severity}</span>
              <span style={{ ...mono, fontSize: 10, fontWeight: 700, color: '#fff',
                minWidth: 54 }}>{a.ticker || 'MARKET'}</span>
              <span style={{ ...mono, fontSize: 10, color: 'var(--text)', flex: 1 }}>
                {a.title}
              </span>
            </div>
            {a.message && (
              <div style={{ ...mono, fontSize: 9, color: 'var(--muted)',
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
    <div className="bb-card" style={{ marginBottom: 14 }}>
      <div className="bb-card-header" style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span>TODAY</span>
        <span style={{ ...mono, fontSize: 9, color: 'var(--muted)' }}>{report?.date}</span>
      </div>
      {missing ? (
        <div style={{ ...mono, fontSize: 10, color: 'var(--yellow)', lineHeight: 1.7 }}>
          REPORT NOT GENERATED for {report.date}.
          <div style={{ color: 'var(--muted)', marginTop: 4 }}>
            {report.note}
            {report.most_recent_available &&
              ` Most recent available: ${report.most_recent_available}.`}
          </div>
        </div>
      ) : (
        <div style={{ ...mono, fontSize: 10, color: 'var(--text-dim)', lineHeight: 1.7 }}>
          {report?.executive_summary || '—'}
        </div>
      )}
      <Link to="/daily-report" style={{ ...mono, fontSize: 9, color: 'var(--orange)',
        textDecoration: 'none', display: 'block', marginTop: 9, letterSpacing: '.14em' }}>
        DAILY REPORT →
      </Link>
    </div>
  )
}

/* ── the page ──────────────────────────────────────────────────────────── */

export default function AriaBrain() {
  const { data: brain, reload: reloadBrain } = useBrain()
  const { data: activity } = useBrainActivity(60)
  const { data: changes } = useWorldChanges(24)
  const { data: report } = useDailyReport()

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto' }}>
      <BrainHeader brain={brain} />

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.4fr) minmax(0,1fr)',
        gap: 16 }} className="aria-intel-grid">
        <div style={{ minWidth: 0 }}>
          {/* SECONDARY — the conversation. The world model, regime, signals,
              memory and vault are already in its context server-side, so
              "why are you bullish on X?" is answerable without the user first
              going somewhere else to look. */}
          <div style={{ ...mono, fontSize: 10, fontWeight: 800, letterSpacing: '.28em',
            color: 'var(--orange)', marginBottom: 8 }}>
            ASK ARIA
          </div>
          <Chat embedded />

          <div style={{ marginTop: 18 }}>
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
