import React, { useEffect, useState, useCallback } from 'react'
import axios from 'axios'

// ─── Small helpers ────────────────────────────────────────────────────────────
const mono = { fontFamily: 'var(--mono)' }

function timeAgo(iso) {
  if (!iso) return '—'
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

const STEP_COLORS = {
  ORIENT: 'var(--orange)', FOCUS: '#4da6ff', RECALL: '#b366ff',
  DECIDE: 'var(--green)', REFLECT: '#ffcc00',
}
function stepColor(step) {
  if (step?.startsWith('ANALYSE')) return '#ff6b9d'
  return STEP_COLORS[step] || 'var(--muted)'
}

// ─── Section 2: one collapsible thought step ─────────────────────────────────
function ThoughtStep({ step }) {
  const [open, setOpen] = useState(false)
  const color = stepColor(step.step)
  const label = step.step.replace('_', ' ')
  return (
    <div style={{ marginBottom: 8 }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
          padding: '6px 10px', background: '#080808',
          border: '1px solid var(--border)', borderLeft: `3px solid ${color}`,
        }}
      >
        <span style={{ ...mono, fontSize: 11, fontWeight: 800, color, letterSpacing: 1, minWidth: 120 }}>
          [{label}]
        </span>
        <span style={{
          ...mono, fontSize: 10, color: 'var(--text-dim)', flex: 1,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {step.conclusion || step.thought?.slice(0, 120)}
        </span>
        <span style={{ ...mono, fontSize: 10, color: '#444' }}>{open ? '▾' : '▸'}</span>
      </div>
      {open && (
        <div style={{
          ...mono, fontSize: 11, color: '#aaa', lineHeight: 1.7,
          padding: '10px 14px', background: '#050505',
          border: '1px solid var(--border)', borderTop: 'none',
          whiteSpace: 'pre-wrap',
        }}>
          {step.thought}
          {step.conclusion && (
            <div style={{ marginTop: 8, color }}>→ {step.conclusion}</div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Section 4: memory row ────────────────────────────────────────────────────
function MemoryRow({ m }) {
  const [open, setOpen] = useState(false)
  const ts = m.timestamp ? new Date(m.timestamp) : null
  const outcomes = Object.values(m.outcome || {}).filter(o => o && o.pnl_pct !== undefined)
  return (
    <div
      onClick={() => setOpen(o => !o)}
      style={{
        padding: '7px 10px', borderBottom: '1px solid #111', cursor: 'pointer',
        background: open ? '#0a0a0a' : 'transparent',
      }}
    >
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ ...mono, fontSize: 10, color: '#555', minWidth: 110 }}>
          {ts ? `${ts.toISOString().slice(0, 10)} ${ts.toTimeString().slice(0, 5)}` : '—'}
        </span>
        <span style={{ ...mono, fontSize: 10, color: '#4da6ff', minWidth: 90 }}>{m.regime || '—'}</span>
        <span style={{ ...mono, fontSize: 10, color: 'var(--text-dim)', flex: 1 }}>
          {(m.tickers_considered || []).join(', ') || '—'}
        </span>
        <span style={{
          ...mono, fontSize: 9, fontWeight: 800, letterSpacing: 1,
          color: m.action_taken === 'PROPOSED_TRADE' ? 'var(--orange)' : 'var(--muted)',
        }}>
          {m.action_taken}
        </span>
        {outcomes.map((o, i) => (
          <span key={i} style={{
            ...mono, fontSize: 10, fontWeight: 800,
            color: o.pnl_pct >= 0 ? 'var(--green)' : 'var(--red)',
          }}>
            {o.pnl_pct >= 0 ? '✓' : '✗'} {o.pnl_pct >= 0 ? '+' : ''}{o.pnl_pct}%
          </span>
        ))}
      </div>
      {open && (
        <div style={{ ...mono, fontSize: 10, color: '#888', lineHeight: 1.6, marginTop: 6, whiteSpace: 'pre-wrap' }}>
          {m.cycle_summary}
        </div>
      )}
    </div>
  )
}

// ─── Control button ───────────────────────────────────────────────────────────
function Btn({ children, onClick, disabled, color = 'var(--orange)' }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        ...mono, background: 'transparent', border: `1px solid ${disabled ? '#222' : color}`,
        color: disabled ? '#444' : color, borderRadius: 3, padding: '6px 14px',
        fontSize: 11, fontWeight: 800, letterSpacing: 1,
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
    >
      {children}
    </button>
  )
}

// ─── Section 4b: Obsidian vault browser ──────────────────────────────────────
function VaultBrowser({ busy }) {
  const [stats, setStats] = useState(null)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [working, setWorking] = useState(false)
  const [note, setNote] = useState('')

  useEffect(() => {
    axios.get('/api/vault/status').then(r => setStats(r.data)).catch(() => {})
  }, [])

  const search = () => {
    if (!query.trim()) { setResults([]); return }
    setWorking(true)
    axios.get('/api/vault/search', { params: { q: query.trim(), n: 6 } })
      .then(r => { setResults(r.data?.results || []); setNote('') })
      .catch(e => setNote(`⚠ ${e.response?.data?.detail || e.message}`))
      .finally(() => setWorking(false))
  }

  const reindex = () => {
    setWorking(true); setNote('Indexing vault…')
    axios.post('/api/vault/reindex')
      .then(r => {
        setNote(`Indexed ${r.data.notes_indexed} notes (${r.data.chunks} chunks)`)
        axios.get('/api/vault/status').then(s => setStats(s.data)).catch(() => {})
      })
      .catch(e => setNote(`⚠ ${e.response?.data?.detail || e.message}`))
      .finally(() => setWorking(false))
  }

  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ ...mono, fontSize: 11, fontWeight: 800, color: 'var(--orange)', letterSpacing: 2, marginBottom: 8 }}>
        ▌KNOWLEDGE VAULT (OBSIDIAN)
      </div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') search() }}
          placeholder="Search your vault — e.g. 'options strategy', 'risk management rules'"
          style={{
            ...mono, flex: 1, minWidth: 260, background: '#0d0d0d', border: '1px solid #222',
            color: '#ddd', fontSize: 11, padding: '7px 10px', borderRadius: 3, outline: 'none',
          }}
        />
        <Btn onClick={search} disabled={busy || working}>SEARCH</Btn>
        <Btn onClick={reindex} disabled={busy || working} color="#b366ff">⟳ REINDEX</Btn>
        <span style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
          {stats?.notes_indexed != null
            ? `${stats.notes_indexed} notes · ${stats.chunks} chunks`
            : stats?.vault_exists ? 'not indexed yet' : 'vault not found'}
        </span>
        {note && <span style={{ ...mono, fontSize: 10, color: 'var(--yellow)' }}>{note}</span>}
      </div>
      {results.length > 0 && (
        <div style={{ border: '1px solid var(--border)', background: '#060606', maxHeight: 300, overflowY: 'auto' }}>
          {results.map((r, i) => (
            <div key={i} style={{ padding: '8px 10px', borderBottom: '1px solid #111' }}>
              <div style={{ ...mono, fontSize: 10, marginBottom: 4 }}>
                <span style={{ color: '#b366ff', fontWeight: 800 }}>{r.title}</span>
                <span style={{ color: '#444', marginLeft: 8 }}>{r.path}</span>
              </div>
              <div style={{ ...mono, fontSize: 10, color: '#999', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
                {r.snippet}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────
export default function Brain() {
  const [status, setStatus] = useState(null)
  const [cycle, setCycle] = useState(null)
  const [memories, setMemories] = useState([])
  const [memQuery, setMemQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [intervalMin, setIntervalMin] = useState(15)
  const [model, setModel] = useState('qwen2.5-coder:7b')
  const [ftOpen, setFtOpen] = useState(false)

  const daemon = status?.daemon || {}
  const ollamaUp = status?.ollama_running

  const poll = useCallback(() => {
    axios.get('/api/brain/status').then(r => {
      setStatus(r.data)
      if (r.data?.daemon?.interval_minutes) setIntervalMin(r.data.daemon.interval_minutes)
      if (r.data?.daemon?.model) setModel(r.data.daemon.model)
    }).catch(() => setStatus(null))
    axios.get('/api/brain/last-cycle').then(r => setCycle(r.data)).catch(() => {})
  }, [])

  const loadMemories = useCallback((q) => {
    const req = q
      ? axios.get('/api/brain/recall', { params: { q, n: 20 } })
      : axios.get('/api/brain/memories', { params: { n: 20 } })
    req.then(r => setMemories(r.data?.memories || [])).catch(() => setMemories([]))
  }, [])

  useEffect(() => {
    poll(); loadMemories()
    const id = setInterval(poll, 10000)
    return () => clearInterval(id)
  }, [poll, loadMemories])

  const call = async (fn, okMsg) => {
    setBusy(true); setNotice('')
    try {
      const r = await fn()
      setNotice(r?.data?.message || okMsg)
      poll()
    } catch (e) {
      setNotice(`⚠ ${e.response?.data?.detail || e.message}`)
    } finally { setBusy(false) }
  }

  const runNow = () => call(() => axios.post('/api/brain/run-now'), 'Cycle started')
  const startBrain = () => call(() => axios.post(`/api/brain/start?model=${encodeURIComponent(model)}`), 'Brain started')
  const stopBrain = () => call(() => axios.post('/api/brain/stop'), 'Brain paused')
  const applyInterval = () => call(() => axios.patch(`/api/brain/interval?minutes=${intervalMin}`), `Interval set to ${intervalMin} min`)
  const genTraining = () => call(() => axios.post('/api/brain/generate-training-data'), 'Generating training data…')

  const steps = cycle?.thinking_steps || []
  const models = status?.models || []

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto' }}>

      {/* ── Section 1: Vital signs ─────────────────────────────────────── */}
      <div style={{
        border: '1px solid var(--border)', background: '#070707',
        padding: '12px 16px', marginBottom: 14,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
          <span style={{ ...mono, fontSize: 14, fontWeight: 800, color: 'var(--orange)', letterSpacing: 1 }}>
            ◈ ARIA COGNITIVE BRAIN
          </span>
          {daemon.running ? (
            <span style={{ ...mono, fontSize: 11, fontWeight: 800, color: 'var(--green)' }}>
              ● {daemon.thinking ? 'THINKING' : 'IDLE'}
            </span>
          ) : (
            <span style={{ ...mono, fontSize: 11, fontWeight: 800, color: 'var(--red)' }}>○ OFFLINE</span>
          )}
          <span style={{ ...mono, fontSize: 11, color: 'var(--text-dim)' }}>
            Cycle #{cycle?.cycle_count ?? daemon.cycle_count ?? 0}
          </span>
          <span style={{ ...mono, fontSize: 11, color: 'var(--text-dim)' }}>
            Last: {timeAgo(cycle?.timestamp)}
          </span>
          {!daemon.running && (
            <Btn onClick={startBrain} disabled={busy || !ollamaUp} color="var(--green)">▶ START</Btn>
          )}
        </div>
        <div style={{ display: 'flex', gap: 18, marginTop: 8, flexWrap: 'wrap' }}>
          <span style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
            Regime: <span style={{ color: '#4da6ff' }}>{cycle?.regime || '—'}</span>
          </span>
          <span style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
            VIX: <span style={{ color: '#ddd' }}>{cycle?.vix ?? '—'}</span>
          </span>
          <span style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
            Memories: <span style={{ color: '#ddd' }}>{cycle?.memory_count ?? daemon.memory_count ?? '—'}</span>
          </span>
          <span style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
            Model: <span style={{ color: 'var(--orange)' }}>{daemon.model || model}</span>
          </span>
          <span style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
            Ollama: <span style={{ color: ollamaUp ? 'var(--green)' : 'var(--red)' }}>
              {ollamaUp ? 'CONNECTED' : 'OFFLINE'}
            </span>
          </span>
        </div>
        {cycle?.error && (
          <div style={{ ...mono, fontSize: 10, color: 'var(--red)', marginTop: 8 }}>
            ⚠ Last cycle error: {cycle.error}
          </div>
        )}
      </div>

      {/* ── Section 3: Control panel ───────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        border: '1px solid var(--border)', background: '#070707',
        padding: '10px 16px', marginBottom: 14,
      }}>
        <Btn onClick={runNow} disabled={busy || !ollamaUp || daemon.thinking}>▶ RUN NOW</Btn>
        {daemon.running
          ? <Btn onClick={stopBrain} disabled={busy} color="var(--red)">⏸ PAUSE</Btn>
          : <Btn onClick={startBrain} disabled={busy || !ollamaUp} color="var(--green)">▶ START</Btn>}
        <span style={{ ...mono, fontSize: 10, color: 'var(--muted)', marginLeft: 8 }}>Interval:</span>
        <input
          type="number" min="1" max="1440" value={intervalMin}
          onChange={e => setIntervalMin(Number(e.target.value))}
          style={{
            ...mono, width: 52, background: '#0d0d0d', border: '1px solid #222',
            color: '#ddd', fontSize: 11, padding: '5px 6px', borderRadius: 3,
          }}
        />
        <span style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>min</span>
        <Btn onClick={applyInterval} disabled={busy}>SET</Btn>
        <span style={{ ...mono, fontSize: 10, color: 'var(--muted)', marginLeft: 8 }}>Model:</span>
        <select
          value={model} onChange={e => setModel(e.target.value)}
          style={{
            ...mono, background: '#0d0d0d', border: '1px solid #222',
            color: 'var(--orange)', fontSize: 11, padding: '5px 6px', borderRadius: 3,
          }}
        >
          {(models.length ? models : [{ name: model }]).map(m => (
            <option key={m.name} value={m.name}>{m.name}</option>
          ))}
        </select>
        {notice && <span style={{ ...mono, fontSize: 10, color: 'var(--yellow)' }}>{notice}</span>}
      </div>

      {/* ── Section 2: Live thought stream ─────────────────────────────── */}
      <div style={{ marginBottom: 14 }}>
        <div style={{
          ...mono, fontSize: 11, fontWeight: 800, color: 'var(--orange)',
          letterSpacing: 2, marginBottom: 8,
        }}>
          ▌LIVE THOUGHT STREAM {daemon.thinking && <span style={{ color: 'var(--green)' }}>· thinking…</span>}
        </div>
        {steps.length === 0 ? (
          <div style={{
            ...mono, fontSize: 11, color: '#444', border: '1px dashed #1a1a1a',
            padding: '18px 16px', textAlign: 'center',
          }}>
            No reasoning cycle recorded yet. {ollamaUp ? 'Press RUN NOW to make the brain think.' : 'Start Ollama, then start the brain.'}
          </div>
        ) : (
          steps.map((s, i) => <ThoughtStep key={i} step={s} />)
        )}
        {cycle?.decisions?.length > 0 && (
          <div style={{
            border: '1px solid var(--border)', borderLeft: '3px solid var(--green)',
            background: '#080808', padding: '8px 12px', marginTop: 10,
          }}>
            <div style={{ ...mono, fontSize: 10, fontWeight: 800, color: 'var(--green)', letterSpacing: 1, marginBottom: 6 }}>
              DECISIONS · {cycle.trades_queued || 0} trade(s) queued for approval
            </div>
            {cycle.decisions.map((d, i) => (
              <div key={i} style={{ ...mono, fontSize: 11, color: 'var(--text-dim)', lineHeight: 1.8 }}>
                <span style={{ color: '#ddd', fontWeight: 700 }}>{d.ticker}</span>
                {' | '}
                <span style={{
                  color: d.action === 'PROPOSE_BUY' ? 'var(--green)'
                    : d.action === 'PROPOSE_SELL' ? 'var(--red)' : 'var(--muted)',
                  fontWeight: 700,
                }}>{d.action}</span>
                {' | '}{d.conviction}{' | '}{d.reason}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Section 4: Memory browser ──────────────────────────────────── */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ ...mono, fontSize: 11, fontWeight: 800, color: 'var(--orange)', letterSpacing: 2, marginBottom: 8 }}>
          ▌MEMORY BROWSER
        </div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <input
            value={memQuery}
            onChange={e => setMemQuery(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') loadMemories(memQuery.trim()) }}
            placeholder="Semantic search — e.g. 'high VIX crypto sell-off'  (Enter to search, empty for recent)"
            style={{
              ...mono, flex: 1, background: '#0d0d0d', border: '1px solid #222',
              color: '#ddd', fontSize: 11, padding: '7px 10px', borderRadius: 3, outline: 'none',
            }}
          />
          <Btn onClick={() => loadMemories(memQuery.trim())} disabled={busy}>SEARCH</Btn>
        </div>
        <div style={{ border: '1px solid var(--border)', background: '#060606', maxHeight: 300, overflowY: 'auto' }}>
          {memories.length === 0 ? (
            <div style={{ ...mono, fontSize: 11, color: '#444', padding: '14px 16px', textAlign: 'center' }}>
              No memories yet — the brain remembers each completed reasoning cycle.
            </div>
          ) : (
            memories.map((m, i) => <MemoryRow key={m.id || i} m={m} />)
          )}
        </div>
      </div>

      {/* ── Section 4b: Obsidian knowledge vault ───────────────────────── */}
      <VaultBrowser busy={busy} />

      {/* ── Section 5: Fine-tune panel ─────────────────────────────────── */}
      <div style={{ marginBottom: 30 }}>
        <div
          onClick={() => setFtOpen(o => !o)}
          style={{ ...mono, fontSize: 11, fontWeight: 800, color: 'var(--orange)', letterSpacing: 2, marginBottom: 8, cursor: 'pointer' }}
        >
          ▌FINE-TUNE (OPTIONAL) {ftOpen ? '▾' : '▸'}
        </div>
        {ftOpen && (
          <div style={{ border: '1px solid var(--border)', background: '#070707', padding: '12px 16px' }}>
            <div style={{ ...mono, fontSize: 10, color: 'var(--muted)', lineHeight: 1.8, marginBottom: 10 }}>
              Fine-tune a local model on this system's signal history using Unsloth (LoRA).
              Requires a CUDA build of PyTorch on the RTX 4060. Install:
            </div>
            <pre style={{
              ...mono, fontSize: 10, color: '#8f8', background: '#020202',
              border: '1px solid #1a1a1a', padding: '10px 12px', overflowX: 'auto', marginBottom: 10,
            }}>
{`venv\\Scripts\\pip.exe uninstall torch -y
venv\\Scripts\\pip.exe install torch --index-url https://download.pytorch.org/whl/cu121
venv\\Scripts\\pip.exe install unsloth`}
            </pre>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <Btn onClick={genTraining} disabled={busy}>⚙ GENERATE TRAINING DATA</Btn>
              <span style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
                Samples ready: <span style={{ color: '#ddd' }}>{status?.training_samples ?? 0}</span>
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
