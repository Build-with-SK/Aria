/**
 * pages/PortfolioWorkspace.jsx — positions, risk, stress and the desk, as one page.
 *
 * Four nav entries collapse here: PORTFOLIO, STRESS, THE DESK and its APPROVAL
 * QUEUE. They were never four subjects. The desk opens and closes the
 * positions; the stress panel shocks those same positions; the approval queue
 * is the step between the desk proposing and the broker filling. Splitting them
 * across the rail made it possible — and this actually happened — to run the
 * desk without ever noticing the queue.
 *
 * Sections, in the order a risk question is actually asked (§7):
 *
 *      What do I hold?          the account, from the broker
 *      What is my risk?         exposure, concentration, drawdown — measured,
 *                               or explicitly not measurable
 *      What would hurt me?      the stress scenarios
 *      What needs a decision?   the approval queue, at the top when non-empty
 *      What is the desk doing?  its cycle, slate and last tick
 *
 * The safety contract is unchanged and is enforced in code, not here: nothing
 * on this page can place an order a human has not approved.
 */
import React, { useEffect, useState } from 'react'
import axios from 'axios'
import { useWorld } from '../hooks/useApi'
import { SectionHeader } from '../components/UI'
import Portfolio from './Portfolio'
import Quant from './Quant'
import Desk from './Desk'
import Execution from './Execution'

const mono = { fontFamily: 'var(--mono)' }

const Risk = ({ label, value, sub, color = '#fff' }) => (
  <div className="bb-card" style={{ padding: '10px 13px' }}>
    <div style={{ ...mono, fontSize: 9, letterSpacing: '.15em', color: 'var(--muted)' }}>{label}</div>
    <div style={{ ...mono, fontSize: 18, fontWeight: 800, color, marginTop: 3 }}>{value}</div>
    {sub && <div style={{ ...mono, fontSize: 9, color: 'var(--muted)', marginTop: 2, lineHeight: 1.45 }}>{sub}</div>}
  </div>
)

/* The risk header reads from the world model, so an unmeasurable quantity says
   why rather than rendering a zero that looks like a measurement. */
function RiskState({ world }) {
  const p = world?.portfolio
  if (!p) return null
  const conc = p.concentration || {}
  const dd = p.drawdown_from_peak

  return (
    <>
      <SectionHeader>RISK STATE</SectionHeader>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(155px,1fr))',
                    gap: 10, marginBottom: 14 }}>
        <Risk label="OPEN POSITIONS" value={p.open_positions ?? 0} color="var(--orange)"
              sub={p.day?.trades_today != null ? `${p.day.trades_today} trades today` : null} />
        <Risk label="EQUITY"
              value={p.equity != null ? Number(p.equity).toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—'}
              sub={p.peak_equity != null ? `peak ${Number(p.peak_equity).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : null} />
        <Risk label="DRAWDOWN"
              value={dd != null ? `${(dd * 100).toFixed(1)}%` : '—'}
              color={dd == null ? 'var(--muted)' : dd < -0.05 ? 'var(--red)' : 'var(--green)'}
              sub={dd == null ? 'no equity history yet' : 'from peak'} />
        <Risk label="LARGEST WEIGHT"
              value={conc.measurable ? `${(conc.largest_weight * 100).toFixed(1)}%` : '—'}
              color={conc.measurable ? 'var(--yellow)' : 'var(--muted)'}
              sub={conc.measurable ? `top 3 = ${(conc.top3_weight * 100).toFixed(1)}%` : conc.why} />
        <Risk label="DAY NOTIONAL"
              value={p.day?.notional_used != null ? Number(p.day.notional_used).toFixed(0) : '—'}
              sub="used against the daily budget" />
      </div>
    </>
  )
}

export default function PortfolioWorkspace() {
  const { data: world } = useWorld()
  const [pending, setPending] = useState(0)

  // A trade waiting on a human is the only time-sensitive thing on this page,
  // so the queue is polled and hoisted above everything else when it is not
  // empty — rather than sitting behind a tab nobody opens.
  useEffect(() => {
    const poll = () => axios.get('/api/execute/queue?status=pending')
      .then(r => setPending(r.data?.count || 0)).catch(() => {})
    poll()
    const id = setInterval(poll, 20000)
    return () => clearInterval(id)
  }, [])

  return (
    <div style={{ maxWidth: 1240, margin: '0 auto' }}>
      <div style={{ marginBottom: 12 }}>
        <div style={{ ...mono, fontSize: 16, fontWeight: 800, color: 'var(--orange)', letterSpacing: '.12em' }}>
          ▣ PORTFOLIO
        </div>
        <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
          Holdings, risk, stress and the desk that manages them.
        </div>
      </div>

      <RiskState world={world} />

      {/* Approvals first when something is waiting — that is the whole point of
          hoisting it out of a tab. */}
      {pending > 0 && (
        <>
          <SectionHeader color="var(--yellow)">
            {pending} DECISION{pending === 1 ? '' : 'S'} WAITING ON YOU
          </SectionHeader>
          <div style={{ marginBottom: 18 }}><Execution /></div>
        </>
      )}

      <SectionHeader>HOLDINGS</SectionHeader>
      <div style={{ marginBottom: 18 }}><Portfolio /></div>

      <SectionHeader>STRESS SCENARIOS</SectionHeader>
      {/* The heading used to read "what would hurt THIS portfolio", which is
          not what the numbers below are. src/gs_quant_bridge/scenario_engine.py
          builds a hypothetical book out of data/signals.json — weights from
          composite scores, a "rough notional" per name — and shocks that. With
          the account 100% cash it was reporting ±£5–7k of P&L on positions
          nobody holds, under a heading that claimed otherwise.

          The engine is genuinely useful: it says which exposures are fragile
          to which shock. It just is not a statement about the account, so it
          says so, and says so loudest exactly when the two differ most. */}
      <div className="bb-card" style={{
        marginBottom: 10,
        borderColor: (world?.portfolio?.open_positions ?? 0) === 0
          ? 'rgba(255,179,36,.35)' : 'var(--border)',
      }}>
        <div style={{ ...mono, fontSize: 10, color: 'var(--text-dim)', lineHeight: 1.65 }}>
          {(world?.portfolio?.open_positions ?? 0) === 0 ? (
            <>
              <span style={{ color: 'var(--yellow)' }}>⚠ These shocks are not applied to your account.</span>
              {' '}You hold nothing right now, so there is no book to stress. The
              figures below shock a hypothetical portfolio built from the signal
              scores in signals.json — read them as “which exposures are fragile
              to which shock”, never as your P&amp;L.
            </>
          ) : (
            <>Scenarios are computed over a signal-weighted book, not your exact
              position sizes. Directions and relative magnitudes are meaningful;
              the absolute P&amp;L is indicative.</>
          )}
        </div>
      </div>
      <div style={{ marginBottom: 18 }}><Quant /></div>

      <SectionHeader>THE DESK</SectionHeader>
      <div style={{ marginBottom: 18 }}><Desk /></div>

      {/* When nothing is pending the queue still belongs on the page — an empty
          queue is information, and hiding it re-creates the problem where the
          desk could run unobserved. */}
      {pending === 0 && (
        <>
          <SectionHeader>APPROVAL QUEUE</SectionHeader>
          <Execution />
        </>
      )}
    </div>
  )
}
