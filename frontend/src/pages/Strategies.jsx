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
import React, { useState } from 'react'
import axios from 'axios'
import { Empty, SectionHeader, Spinner } from '../components/UI'
import { useApiData, useWorld } from '../hooks/useApi'
import { DataUnavailable } from '../components/DataState'

const mono = { fontFamily: 'var(--font)' }

const Num = ({ v, digits = 2, good }) => {
  if (v == null || !Number.isFinite(Number(v))) return <span style={{ color: 'var(--ink-3)' }}>—</span>
  const n = Number(v)
  const color = good == null ? 'var(--ink)' : (n >= good ? 'var(--green)' : 'var(--red)')
  return <span style={{ color, fontWeight: 600 }}>{n.toFixed(digits)}</span>
}

export default function Strategies() {
  const { data: world } = useWorld()
  const { data: strategies, loading, error, reload: reloadStrategies } = useApiData('/api/quant/strategies')
  const { data: campaigns, error: campaignError, reload: reloadCampaigns } = useApiData('/api/evolution/campaigns')
  const { data: papers, reload: reloadPapers } = useApiData('/api/quant/papers')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  const load = () => {
    reloadStrategies(); reloadCampaigns(); reloadPapers()
  }

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
  // Auto-execute and the slate come from /api/world. When that has not
  // answered, the desk's state is UNKNOWN — showing it as OFF / 0 picks told
  // the owner the desk was idle while it was armed and trading.
  const autoKnown = typeof desk?.auto_execute === 'boolean'
  const slateKnown = desk?.slate_size != null && Number.isFinite(Number(desk.slate_size))
  const camps = Array.isArray(campaigns) ? campaigns : (campaigns?.campaigns || [])

  // A challenger has to beat the gate, not merely exist. Sorting by
  // out-of-sample Sharpe puts the only number that matters at the top.
  const ranked = [...rows].sort((a, b) =>
    ((b?.metrics?.oos?.sharpe ?? -99) - (a?.metrics?.oos?.sharpe ?? -99)))
  const survivors = ranked.filter(s => (s?.metrics?.oos?.sharpe ?? -99) > 0.5)

  if (loading && !strategies) return <Spinner />
  if (error || strategies == null || (!Array.isArray(strategies) && !Array.isArray(strategies.strategies))) {
    return <DataUnavailable name="Strategies" error={error} />
  }

  return (
    <div style={{ maxWidth: 1160, margin: '0 auto' }}>
      <div style={{ marginBottom: 12 }}>
        <div style={{ ...mono, fontSize: 17, fontWeight: 600, color: 'var(--accent)', }}>
          ⚗ Strategies
        </div>
        <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>
          What is live, what is competing to replace it, and what did not survive validation.
        </div>
      </div>

      {/* ── CHAMPION ── */}
      <SectionHeader>Champion · what is actually trading</SectionHeader>
      <div className="bb-card" style={{ marginBottom: 14 }}>
        <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap' }}>
          <div>
            {/* There is no `mode` key in desk_config.json — the first version
                of this card asked for one and printed an em dash forever.
                These two are real, and they are the two that decide how much
                the desk can do before a human is involved. */}
            <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', }}>Min conviction</div>
            <div style={{ ...mono, fontSize: 16, fontWeight: 600, color: 'var(--accent)' }}>
              {desk?.min_conviction ?? '—'}
            </div>
          </div>
          <div>
            <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', }}>Daily budget</div>
            <div style={{ ...mono, fontSize: 16, fontWeight: 600, color: 'var(--ink)' }}>
              {desk?.daily_notional_budget != null
                ? Number(desk.daily_notional_budget).toLocaleString(undefined, { maximumFractionDigits: 0 })
                : '—'}
            </div>
          </div>
          <div>
            <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', }}>Auto-execute</div>
            <div style={{ ...mono, fontSize: 16, fontWeight: 600,
                          color: !autoKnown ? 'var(--ink-3)'
                            : desk.auto_execute ? 'var(--yellow)' : 'var(--green)' }}>
              {!autoKnown ? '—' : desk.auto_execute ? 'Armed' : 'Off'}
            </div>
            {!autoKnown && (
              <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>unavailable</div>
            )}
          </div>
          <div>
            <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', }}>Slate</div>
            <div style={{ ...mono, fontSize: 16, fontWeight: 600,
                          color: slateKnown ? 'var(--ink)' : 'var(--ink-3)' }}>
              {slateKnown ? `${desk.slate_size} picks` : '—'}
            </div>
          </div>
          <div>
            <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', }}>Catalogued</div>
            <div style={{ ...mono, fontSize: 16, fontWeight: 600, color: 'var(--ink)' }}>
              {world?.strategies?.catalogued ?? rows.length}
            </div>
          </div>
        </div>
        <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', marginTop: 10, lineHeight: 1.6 }}>
          Nothing on this page places a trade. When armed, auto-execute trades
          the paper account only — enforced in code, not by this page. Any order
          on a live account needs your approval.
        </div>
      </div>

      {/* ── CHALLENGERS ── */}
      <SectionHeader>
        Challengers · {survivors.length} of {ranked.length} past the gate
      </SectionHeader>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
        <button onClick={runLab} disabled={busy}
          style={{ ...mono, fontSize: 12, padding: '7px 14px',
                   background: busy ? 'var(--inset)' : 'color-mix(in srgb, var(--accent) 12%, transparent)',
                   border: '1px solid var(--accent)', color: 'var(--accent)',
                   borderRadius: 10, cursor: busy ? 'wait' : 'pointer' }}>
          {busy ? 'Requesting…' : 'Run a lab cycle'}
        </button>
        <span style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>
          {lab?.runs != null ? `${lab.runs} cycles · last ${lab.last_run ? new Date(lab.last_run).toLocaleString() : '—'}` : ''}
          {papers?.length ? ` · ${papers.length} papers read` : ''}
        </span>
        {note && <span style={{ ...mono, fontSize: 12, color: 'var(--yellow)' }}>{note}</span>}
      </div>

      <div className="bb-card" style={{ marginBottom: 14, overflowX: 'auto' }}>
        {!ranked.length && (
          <div style={{ ...mono, fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.7 }}>
            The lab has catalogued no strategies yet. It reads arXiv q-fin
            papers, maps them to templates and backtests them; a cycle that
            finds nothing publishable is a normal outcome, not a failure.
          </div>
        )}
        {!!ranked.length && (
          <table style={{ width: '100%', borderCollapse: 'collapse', ...mono, fontSize: 13 }}>
            <thead>
              <tr style={{ color: 'var(--ink-3)', fontSize: 12, textAlign: 'left' }}>
                <th style={{ padding: '5px 6px' }}>Strategy</th>
                <th style={{ padding: '5px 6px' }}>Origin</th>
                <th style={{ padding: '5px 6px', textAlign: 'right' }}>Full Sharpe</th>
                <th style={{ padding: '5px 6px', textAlign: 'right' }}>OOS Sharpe</th>
                <th style={{ padding: '5px 6px', textAlign: 'right' }}>Max DD</th>
                <th style={{ padding: '5px 6px' }}>Verdict</th>
              </tr>
            </thead>
            <tbody>
              {ranked.slice(0, 40).map((s, i) => {
                const oos = s?.metrics?.oos?.sharpe
                const passes = (oos ?? -99) > 0.5
                return (
                  <tr key={s.id || i} style={{ borderTop: '1px solid var(--inset)' }}>
                    <td style={{ padding: '5px 6px', color: 'var(--ink)', fontWeight: 600 }}>
                      {/* `name` is "<template> · <paper title>", and ORIGIN
                          already carries the title — printing the whole thing
                          here rendered every row twice as wide as it needed to
                          be, with the same sentence in both columns. */}
                      {s.template || (s.name || s.id || 'Unnamed strategy').split(' · ')[0]}
                    </td>
                    <td style={{ padding: '5px 6px', color: 'var(--ink-3)', maxWidth: 260,
                                 overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {s?.paper?.title || s.template || '—'}
                    </td>
                    <td style={{ padding: '5px 6px', textAlign: 'right' }}>
                      <Num v={s?.metrics?.full?.sharpe} />
                    </td>
                    <td style={{ padding: '5px 6px', textAlign: 'right' }}>
                      <Num v={oos} good={0.5} />
                    </td>
                    <td style={{ padding: '5px 6px', textAlign: 'right', color: 'var(--ink-3)' }}>
                      {s?.metrics?.oos?.max_dd_pct != null
                        ? `${Number(s.metrics.oos.max_dd_pct).toFixed(0)}%` : '—'}
                    </td>
                    <td style={{ padding: '5px 6px' }}>
                      <span className={`score-pill ${passes ? 'pill-bull' : 'pill-neut'}`}
                            style={{ fontSize: 12 }}>
                        {passes ? 'Challenger' : 'Rejected'}
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
      <SectionHeader>Evolution campaigns</SectionHeader>
      {(campaignError || campaigns == null) && <DataUnavailable name="Evolution campaigns" error={campaignError} />}
      <div className="bb-card">
        {!camps.length && (
          <div style={{ ...mono, fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.7 }}>
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
            <div key={c.started_at || i} style={{ padding: '7px 0', borderBottom: '1px solid var(--inset)' }}>
              <div style={{ display: 'flex', gap: 12, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <span style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', minWidth: 96 }}>
                  {(c.started_at || '').slice(0, 16).replace('T', ' ')}
                </span>
                <span style={{ ...mono, fontSize: 12, color: 'var(--ink-2)', flex: 1 }}>
                  {c.generations} generations · population {c.population} ·
                  {' '}{c.evaluated} evaluated · {c.holdout_days}d holdout
                </span>
                <span style={{ ...mono, fontSize: 13, fontWeight: 600,
                               color: nSurvivors > 0 ? 'var(--green)' : 'var(--ink-3)' }}>
                  {nSurvivors} survivors
                </span>
              </div>
              {best?.genome_key && (
                <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', marginTop: 3,
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
