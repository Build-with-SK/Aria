import React, { useState } from 'react'
import { useQuantStatus, useQuantStrategies, useQuantPapers, quantRunNow } from '../hooks/useApi'
import Term from '../components/Term'

/* ─────────────────────────────────────────────────────────────────────────
   QUANT LAB — the self-learning researcher's console.
   Fully automated loop: read arXiv q-fin papers → map to a strategy
   template (local LLM or deterministic keywords) → backtest on the NSE
   basket → rank by out-of-sample Sharpe. Research only — nothing here
   places trades; execution stays behind the human-approval queue.
   ───────────────────────────────────────────────────────────────────────── */

function Spark({ curve, color }) {
  if (!curve || curve.length < 3) return <span className="muted mono" style={{ fontSize: 9 }}>—</span>
  const vals = curve.map(p => p[1])
  const min = Math.min(...vals), max = Math.max(...vals)
  const W = 110, H = 26
  const pts = vals.map((v, i) =>
    `${(i / (vals.length - 1)) * W},${H - 2 - ((v - min) / (max - min || 1)) * (H - 4)}`).join(' ')
  return (
    <svg width={W} height={H} style={{ display: 'block' }}>
      <polyline fill="none" stroke={color} strokeWidth="1.2" points={pts} />
    </svg>
  )
}

function Stat({ label, value, color }) {
  return (
    <div>
      <div className="muted mono" style={{ fontSize: 9, letterSpacing: '0.08em' }}>{label}</div>
      <div className="mono" style={{ fontSize: 18, fontWeight: 700, color: color || 'var(--white)' }}>{value}</div>
    </div>
  )
}

export default function QuantLab() {
  const { data: status, reload: reloadStatus } = useQuantStatus()
  const { data: strategies } = useQuantStrategies()
  const { data: papers } = useQuantPapers()
  const [kicked, setKicked] = useState(false)

  const runNow = async () => {
    setKicked(true)
    try { await quantRunNow() } catch { /* surfaced via status poll */ }
    setTimeout(() => { reloadStatus(); setKicked(false) }, 4000)
  }

  const sharpeColor = v => v == null ? 'var(--muted)' : v >= 0.5 ? 'var(--green)' : v >= 0 ? 'var(--yellow)' : 'var(--red)'

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginBottom: 14 }}>
        <div style={{ fontFamily: 'var(--mono)', fontSize: 16, fontWeight: 700, color: 'var(--orange)', letterSpacing: '0.15em' }}>
          ⚗ QUANT LAB
        </div>
        <div style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--muted)', letterSpacing: '0.1em' }}>
          // SELF-LEARNING RESEARCHER — READS PAPERS · BUILDS · BACKTESTS · RANKS
        </div>
      </div>

      {/* Status strip */}
      <div className="bb-card" style={{ marginBottom: 14 }}>
        <div style={{ display: 'flex', gap: 40, alignItems: 'center', flexWrap: 'wrap' }}>
          <Stat label="DAEMON" value={status?.running ? (status?.working ? 'THINKING…' : 'RUNNING') : 'STOPPED'}
                color={status?.running ? 'var(--green)' : 'var(--red)'} />
          <Stat label="CYCLE EVERY" value={status ? `${status.interval_hours}h` : '—'} />
          <Stat label="CYCLES DONE" value={status?.runs ?? '—'} />
          <Stat label="PAPERS READ" value={status?.papers_ingested ?? '—'} color="var(--blue)" />
          <Stat label="STRATEGIES TESTED" value={status?.strategies_tested ?? '—'} color="var(--orange)" />
          <Stat label="LAST RUN" value={status?.last_run ? status.last_run.slice(5, 16).replace('T', ' ') : 'never'} />
          <button onClick={runNow} disabled={kicked || status?.working} style={{
            marginLeft: 'auto', background: 'var(--orange-bg)', color: 'var(--orange)',
            border: '1px solid var(--orange-dim)', fontFamily: 'var(--mono)', fontSize: 10,
            fontWeight: 700, letterSpacing: '0.1em', padding: '8px 16px', cursor: 'pointer', borderRadius: 2,
          }}>
            {kicked || status?.working ? '▸ CYCLE RUNNING…' : '▸ RUN CYCLE NOW'}
          </button>
        </div>
        <div className="muted mono" style={{ fontSize: 9, marginTop: 10 }}>
          {status?.note} · Backtests are simulations of past data, not predictions.
          {status?.last_summary?.errors?.length ? ` · last-run errors: ${status.last_summary.errors.join('; ')}` : ''}
        </div>
      </div>

      {/* Strategy library */}
      <div className="bb-card" style={{ marginBottom: 14 }}>
        <div className="bb-card-header">STRATEGY LIBRARY — RANKED BY OUT-OF-SAMPLE SHARPE (LAST 252 TRADING DAYS)</div>
        {!strategies?.length
          ? <div className="muted mono" style={{ fontSize: 11 }}>no strategies yet — first cycle runs shortly after backend start</div>
          : <table>
              <thead><tr>
                <th>#</th><th><Term>TEMPLATE</Term></th><th><Term>PARAMS</Term></th>
                <th><Term k="oos sharpe">OOS SHARPE</Term></th><th><Term k="cagr">OOS CAGR</Term></th>
                <th><Term k="maxdd">OOS MAXDD</Term></th>
                <th><Term k="sharpe">FULL SHARPE</Term></th><th><Term>EXPOSURE</Term></th>
                <th>EQUITY (3Y)</th><th><Term>MAPPER</Term></th><th><Term k="source paper">SOURCE PAPER</Term></th>
              </tr></thead>
              <tbody>
                {strategies.map((s, i) => {
                  const oos = s.metrics?.oos || {}, full = s.metrics?.full || {}
                  return (
                    <tr key={s.id}>
                      <td className="muted">{i + 1}</td>
                      <td className="acc" style={{ fontWeight: 700 }}>{s.template}</td>
                      <td className="muted" style={{ fontSize: 10 }}>{JSON.stringify(s.params)}</td>
                      <td style={{ color: sharpeColor(oos.sharpe), fontWeight: 700 }}>{oos.sharpe ?? 'N/A'}</td>
                      <td style={{ color: sharpeColor(oos.cagr_pct) }}>{oos.cagr_pct != null ? `${oos.cagr_pct}%` : 'N/A'}</td>
                      <td className="bear">{oos.max_dd_pct != null ? `${oos.max_dd_pct}%` : 'N/A'}</td>
                      <td style={{ color: sharpeColor(full.sharpe) }}>{full.sharpe ?? 'N/A'}</td>
                      <td className="muted">{s.exposure_pct != null ? `${s.exposure_pct}%` : '—'}</td>
                      <td><Spark curve={s.equity_curve} color={sharpeColor(oos.sharpe)} /></td>
                      <td className="muted" style={{ fontSize: 10 }}>{s.mapper}</td>
                      <td style={{ fontSize: 10, maxWidth: 220 }}>
                        <a href={s.paper?.link} target="_blank" rel="noreferrer" style={{ color: 'var(--blue)', textDecoration: 'none' }}>
                          {s.paper?.title?.slice(0, 60)}…
                        </a>
                        {s.also_cited?.length ? <span className="muted"> +{s.also_cited.length} more</span> : null}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>}
        <div className="muted mono" style={{ fontSize: 9, marginTop: 8 }}>
          SRC ▸ basket: NSE large-cap 20 · 3y daily bars (Yahoo Finance) · 0.1%/side costs · long-only equal-weight
        </div>
      </div>

      {/* Papers feed */}
      <div className="bb-card">
        <div className="bb-card-header">RESEARCH INTAKE — LATEST ARXIV Q-FIN PAPERS (INDEXED INTO BRAIN MEMORY)</div>
        {!papers?.length
          ? <div className="muted mono" style={{ fontSize: 11 }}>no papers ingested yet</div>
          : papers.slice(0, 12).map(p => (
            <div key={p.id} style={{ padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
              <a href={p.link} target="_blank" rel="noreferrer" style={{ color: 'var(--text)', fontSize: 12, textDecoration: 'none' }}>
                {p.title}
              </a>
              <div className="muted mono" style={{ fontSize: 9, marginTop: 2 }}>
                {p.id} · {p.published?.slice(0, 10)} · {(p.authors || []).slice(0, 3).join(', ')}
              </div>
            </div>
          ))}
        <div className="muted mono" style={{ fontSize: 9, marginTop: 8 }}>
          SRC ▸ arxiv.org official API · categories q-fin.TR / q-fin.PM / q-fin.ST · embedded into ChromaDB `quant_research`
        </div>
      </div>
    </div>
  )
}
