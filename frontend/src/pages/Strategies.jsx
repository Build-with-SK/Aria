/**
 * pages/Strategies.jsx — the strategy lifecycle in one place (§16, §17, §37).
 *
 * Replaces LabHub, which was a TabBar over the quant lab and its own backtest
 * results — two views of one pipeline behind two clicks. It also absorbs the
 * evolution campaigns, which lived only behind an API nobody surfaced.
 *
 * The champion/challenger framing is honest about where this system actually
 * is: the desk runs a live strategy, the lab breeds candidates, and almost
 * none of them survive the deflated-Sharpe gate. "No survivors" is the
 * expected outcome of an honest gate, not a bug, and the page says so rather
 * than showing an empty table that reads as breakage.
 */
import React, { useEffect, useState } from 'react'
import axios from 'axios'
import { Empty, SectionHeader, Spinner } from '../components/UI'
import { useWorld } from '../hooks/useApi'

const mono = { fontFamily: 'var(--mono)' }

const Num = ({ v, digits = 2, good }) => {
  if (v == null) return <span style={{ color: 'var(--muted)' }}>—</span>
  const n = Number(v)
  const color = good == null ? '#fff' : (n >= good ? 'var(--green)' : 'var(--red)')
  return <span style={{ color, fontWeight: 700 }}>{n.toFixed(digits)}</span>
}

export default function Strategies() {
  const { data: world } = useWorld()
  const [strategies, setStrategies] = useState(null)
  const [campaigns, setCampaigns] = useState(null)
  const [papers, setPapers] = useState(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  const load = () => {
    axios.get('/api/quant/strategies').then(r => setStrategies(r.data)).catch(() => setStrategies([]))
    axios.get('/api/evolution/campaigns').then(r => setCampaigns(r.data)).catch(() => setCampaigns(null))
    axios.get('/api/quant/papers').then(r => setPapers(r.data)).catch(() => setPapers(null))
  }
  useEffect(load, [])

  const runLab = async () => {
    setBusy(true); setNote('')
    try {
      const r = await axios.post('/api/quant/run-now')
      setNote(r.data?.status || 'Lab cycle requested — it runs in the background.')
      setTimeout(load, 4000)
    } catch (e) {
      setNote(`Could not start the lab: ${e?.response?.data?.detail || e.message}`)
    } finally { setBusy(false) }
  }

  const rows = Array.isArray(strategies) ? strategies
    : (strategies?.strategies || [])
  const lab = world?.strategies?.lab
  const desk = world?.strategies?.desk
  const camps = Array.isArray(campaigns) ? campaigns : (campaigns?.campaigns || [])

  // A challenger has to beat the gate, not merely exist. Sorting by
  // out-of-sample Sharpe puts the only number that matters at the top.
  const ranked = [...rows].sort((a, b) =>
    ((b?.metrics?.oos?.sharpe ?? -99) - (a?.metrics?.oos?.sharpe ?? -99)))
  const survivors = ranked.filter(s => (s?.metrics?.oos?.sharpe ?? -99) > 0.5)

  if (!strategies) return <Spinner />

  return (
    <div style={{ maxWidth: 1160, margin: '0 auto' }}>
      <div style={{ marginBottom: 12 }}>
        <div style={{ ...mono, fontSize: 16, fontWeight: 800, color: 'var(--orange)', letterSpacing: '.12em' }}>
          ⚗ STRATEGIES
        </div>
        <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
          What is live, what is competing to replace it, and what did not survive validation.
        </div>
      </div>

      {/* ── CHAMPION ── */}
      <SectionHeader>CHAMPION · WHAT IS ACTUALLY TRADING</SectionHeader>
      <div className="bb-card" style={{ marginBottom: 14 }}>
        <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap' }}>
          <div>
            {/* There is no `mode` key in desk_config.json — the first version
                of this card asked for one and printed an em dash forever.
                These two are real, and they are the two that decide how much
                the desk can do before a human is involved. */}
            <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', letterSpacing: '.15em' }}>MIN CONVICTION</div>
            <div style={{ ...mono, fontSize: 15, fontWeight: 800, color: 'var(--orange)' }}>
              {desk?.min_conviction ?? '—'}
            </div>
          </div>
          <div>
            <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', letterSpacing: '.15em' }}>DAILY BUDGET</div>
            <div style={{ ...mono, fontSize: 15, fontWeight: 800, color: '#fff' }}>
              {desk?.daily_notional_budget != null
                ? Number(desk.daily_notional_budget).toLocaleString(undefined, { maximumFractionDigits: 0 })
                : '—'}
            </div>
          </div>
          <div>
            <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', letterSpacing: '.15em' }}>AUTO-EXECUTE</div>
            <div style={{ ...mono, fontSize: 15, fontWeight: 800,
                          color: desk?.auto_execute ? 'var(--yellow)' : 'var(--green)' }}>
              {desk?.auto_execute ? 'ARMED' : 'OFF'}
            </div>
          </div>
          <div>
            <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', letterSpacing: '.15em' }}>SLATE</div>
            <div style={{ ...mono, fontSize: 15, fontWeight: 800, color: '#fff' }}>
              {desk?.slate_size ?? 0} picks
            </div>
          </div>
          <div>
            <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', letterSpacing: '.15em' }}>CATALOGUED</div>
            <div style={{ ...mono, fontSize: 15, fontWeight: 800, color: '#fff' }}>
              {world?.strategies?.catalogued ?? rows.length}
            </div>
          </div>
        </div>
        <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 10, lineHeight: 1.6 }}>
          Nothing on this page can place a trade. The desk's safety contract is
          enforced in code, not here: auto-execute is paper-only behind an env
          gate, and every order still needs a human approval.
        </div>
      </div>

      {/* ── CHALLENGERS ── */}
      <SectionHeader>
        CHALLENGERS · {survivors.length} OF {ranked.length} PAST THE GATE
      </SectionHeader>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
        <button onClick={runLab} disabled={busy}
          style={{ ...mono, fontSize: 10, letterSpacing: '.12em', padding: '7px 14px',
                   background: busy ? '#141414' : 'rgba(255,36,71,.12)',
                   border: '1px solid var(--orange)', color: 'var(--orange)',
                   borderRadius: 4, cursor: busy ? 'wait' : 'pointer' }}>
          {busy ? 'REQUESTING…' : 'RUN A LAB CYCLE'}
        </button>
        <span style={{ ...mono, fontSize: 9, color: 'var(--muted)' }}>
          {lab?.runs != null ? `${lab.runs} cycles · last ${lab.last_run ? new Date(lab.last_run).toLocaleString() : '—'}` : ''}
          {papers?.length ? ` · ${papers.length} papers read` : ''}
        </span>
        {note && <span style={{ ...mono, fontSize: 10, color: 'var(--yellow)' }}>{note}</span>}
      </div>

      <div className="bb-card" style={{ marginBottom: 14, overflowX: 'auto' }}>
        {!ranked.length && (
          <div style={{ ...mono, fontSize: 11, color: 'var(--muted)', lineHeight: 1.7 }}>
            The lab has catalogued no strategies yet. It reads arXiv q-fin
            papers, maps them to templates and backtests them; a cycle that
            finds nothing publishable is a normal outcome, not a failure.
          </div>
        )}
        {!!ranked.length && (
          <table style={{ width: '100%', borderCollapse: 'collapse', ...mono, fontSize: 11 }}>
            <thead>
              <tr style={{ color: 'var(--muted)', fontSize: 9, letterSpacing: '.12em', textAlign: 'left' }}>
                <th style={{ padding: '5px 6px' }}>STRATEGY</th>
                <th style={{ padding: '5px 6px' }}>ORIGIN</th>
                <th style={{ padding: '5px 6px', textAlign: 'right' }}>FULL SHARPE</th>
                <th style={{ padding: '5px 6px', textAlign: 'right' }}>OOS SHARPE</th>
                <th style={{ padding: '5px 6px', textAlign: 'right' }}>MAX DD</th>
                <th style={{ padding: '5px 6px' }}>VERDICT</th>
              </tr>
            </thead>
            <tbody>
              {ranked.slice(0, 40).map((s, i) => {
                const oos = s?.metrics?.oos?.sharpe
                const passes = (oos ?? -99) > 0.5
                return (
                  <tr key={s.id || i} style={{ borderTop: '1px solid #141414' }}>
                    <td style={{ padding: '5px 6px', color: '#fff', fontWeight: 700 }}>
                      {/* `name` is "<template> · <paper title>", and ORIGIN
                          already carries the title — printing the whole thing
                          here rendered every row twice as wide as it needed to
                          be, with the same sentence in both columns. */}
                      {s.template || (s.name || s.id).split(' · ')[0]}
                    </td>
                    <td style={{ padding: '5px 6px', color: 'var(--muted)', maxWidth: 260,
                                 overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {s?.paper?.title || s.template || '—'}
                    </td>
                    <td style={{ padding: '5px 6px', textAlign: 'right' }}>
                      <Num v={s?.metrics?.full?.sharpe} />
                    </td>
                    <td style={{ padding: '5px 6px', textAlign: 'right' }}>
                      <Num v={oos} good={0.5} />
                    </td>
                    <td style={{ padding: '5px 6px', textAlign: 'right', color: 'var(--muted)' }}>
                      {s?.metrics?.oos?.max_dd_pct != null
                        ? `${Number(s.metrics.oos.max_dd_pct).toFixed(0)}%` : '—'}
                    </td>
                    <td style={{ padding: '5px 6px' }}>
                      <span className={`score-pill ${passes ? 'pill-bull' : 'pill-neut'}`}
                            style={{ fontSize: 9 }}>
                        {passes ? 'CHALLENGER' : 'REJECTED'}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* ── EVOLUTION ── */}
      <SectionHeader>EVOLUTION CAMPAIGNS</SectionHeader>
      <div className="bb-card">
        {!camps.length && (
          <div style={{ ...mono, fontSize: 11, color: 'var(--muted)', lineHeight: 1.7 }}>
            No campaigns recorded. The evolution lab breeds strategy variants and
            filters them on deflated Sharpe — a gate that corrects for how many
            candidates were tried. Reporting “no survivors” is that gate working,
            not the lab failing.
          </div>
        )}
        {/* `survivors` is a LIST of genomes, not a count. Rendering it directly
            threw "Objects are not valid as a React child" and took the whole
            page down behind the error boundary — the field names here now come
            from the payload rather than from what a campaign record ought to
            look like. */}
        {camps.slice(0, 12).map((c, i) => {
          const nSurvivors = Array.isArray(c.survivors) ? c.survivors.length : (c.survivors ?? 0)
          const best = Array.isArray(c.best_in_sample) ? c.best_in_sample[0] : null
          return (
            <div key={c.started_at || i} style={{ padding: '7px 0', borderBottom: '1px solid #141414' }}>
              <div style={{ display: 'flex', gap: 12, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <span style={{ ...mono, fontSize: 9, color: 'var(--muted)', minWidth: 96 }}>
                  {(c.started_at || '').slice(0, 16).replace('T', ' ')}
                </span>
                <span style={{ ...mono, fontSize: 10, color: 'var(--text-dim)', flex: 1 }}>
                  {c.generations} generations · population {c.population} ·
                  {' '}{c.evaluated} evaluated · {c.holdout_days}d holdout
                </span>
                <span style={{ ...mono, fontSize: 11, fontWeight: 700,
                               color: nSurvivors > 0 ? 'var(--green)' : 'var(--muted)' }}>
                  {nSurvivors} survivors
                </span>
              </div>
              {best?.genome_key && (
                <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 3,
                              paddingLeft: 108, wordBreak: 'break-all' }}>
                  best in sample: {String(best.genome_key)}
                  {nSurvivors === 0 && ' — did not survive the holdout, which is the gate working'}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
