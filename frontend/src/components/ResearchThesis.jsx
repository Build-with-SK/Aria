/**
 * components/ResearchThesis.jsx — why a view exists, from
 * /api/intelligence/research/{symbol} (owner-only).
 *
 * WHAT IT MAY SAY
 * The direction comes from INDEPENDENT evidence groups, never from a count of
 * indicators: five price indicators are one group, and the panel says so,
 * with the measured number of effective independent price signals. There is
 * no confidence percentage anywhere — the quality scorecard states, dimension
 * by dimension, what kind of support the view has. Every component shows its
 * freshness and point-in-time status; MISSING, NO_DATA, INSUFFICIENT_DATA and
 * UNKNOWN are printed as such. The thesis is not a signal: it can explain a
 * trade, never authorise or size one.
 */
import React, { useEffect, useState } from 'react'
import axios from 'axios'

const MONO = 'var(--font)'
const mono = { fontFamily: MONO }

const STATE_COLOR = {
  BULLISH: 'var(--green)', BEARISH: 'var(--red)', NEUTRAL: 'var(--ink-3)',
  VALID: 'var(--green)', FRESH: 'var(--green)', ALIGNED: 'var(--green)', LOW: 'var(--green)',
  STRONG: 'var(--green)', MODERATE: 'var(--yellow)', WEAK: 'var(--ink-3)',
  LIMITED: 'var(--yellow)', NARROW: 'var(--yellow)', CONCENTRATED: 'var(--yellow)',
  ELEVATED: 'var(--yellow)', STALE: 'var(--yellow)', LIVE_ONLY: 'var(--yellow)',
  SINGLE_GROUP: 'var(--yellow)', DEGRADED: 'var(--yellow)',
  HIGH: 'var(--red)', CONFLICTED: 'var(--red)', MISSING: 'var(--red)', VIOLATED: 'var(--red)',
}

const Chip = ({ text }) => {
  const c = STATE_COLOR[text] || 'var(--ink-3)'
  return (
    <span style={{ ...mono, fontSize: 12, color: c, border: `1px solid ${c}`,
                   borderRadius: 8, padding: '1px 5px', marginRight: 5, whiteSpace: 'nowrap' }}>
      {String(text ?? 'UNKNOWN').replaceAll('_', ' ')}
    </span>
  )
}

const Label = ({ children }) => (
  <div style={{ ...mono, fontSize: 12, color: 'var(--accent)',
                fontWeight: 600, margin: '12px 0 5px' }}>{children}</div>
)

const Line = ({ children, color = 'var(--ink-2)' }) => (
  <div style={{ ...mono, fontSize: 12, color, lineHeight: 1.6 }}>{children}</div>
)

const pct = x => (x == null ? '—' : `${(x * 100).toFixed(1)}%`)

function EvidenceList({ items, empty }) {
  if (!items?.length) return <Line color="var(--ink-3)">{empty}</Line>
  return items.slice(0, 10).map(i => (
    <div key={i.item_id} style={{ display: 'flex', gap: 8, padding: '2px 0' }}>
      <span style={{ minWidth: 104 }}><Chip text={i.group} /></span>
      <Line>
        <span style={{ color: 'var(--ink)' }}>{i.label}</span> — {i.explanation}
        <span style={{ color: 'var(--ink-3)' }}> · {i.freshness} · {i.reliability?.state}</span>
      </Line>
    </div>
  ))
}

export default function ResearchThesis({ symbol }) {
  const [rep, setRep] = useState(null)
  const [state, setState] = useState('idle')   // idle | loading | ready | denied | error
  const [err, setErr] = useState('')

  useEffect(() => { setRep(null); setState('idle'); setErr('') }, [symbol])

  const run = () => {
    setState('loading'); setErr('')
    axios.get(`/api/intelligence/research/${encodeURIComponent(symbol)}`)
      .then(r => { setRep(r.data); setState('ready') })
      .catch(e => {
        const code = e?.response?.status
        if (code === 401 || code === 403) setState('denied')
        else { setErr(e?.response?.data?.detail || 'research failed'); setState('error') }
      })
  }

  const th = rep?.thesis
  const comps = rep?.snapshot?.components || {}
  const attr = comps.factors?.value?.windows?.['21d']
  const pairs = comps.cross_asset?.value?.pairs || {}
  const ev = comps.event_risk?.value || {}
  const news = comps.news?.value || {}
  const dep = rep?.evidence?.agreement?.price_dependence

  return (
    <div className="bb-card">
      <div className="bb-card-header">Research thesis — independent evidence</div>
      {state === 'idle' && (
        <div style={{ padding: '10px 2px' }}>
          <button onClick={run} style={{
            ...mono, fontSize: 13, fontWeight: 600, padding: '7px 16px', cursor: 'pointer',
            background: 'none', border: '1px solid var(--accent)', color: 'var(--accent)', borderRadius: 10,
          }}>Build research thesis for {symbol}</button>
          <div style={{ marginTop: 7 }}>
            <Line color="var(--ink-3)">
              Assembles point-in-time evidence (price, market and factors, fundamentals,
              macro, news stories, cross-asset, events, recorded model calls), groups it by
              what it is derived from, and explains the view. Fetches market data; may take
              a while the first time.
            </Line>
          </div>
        </div>
      )}
      {state === 'loading' && <Line>assembling evidence…</Line>}
      {state === 'denied' && <Line color="var(--ink-3)">The research thesis reads the owner&apos;s
        prediction record. Owner sign-in required.</Line>}
      {state === 'error' && <Line color="var(--red)">{err}</Line>}

      {state === 'ready' && th && <>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 4 }}>
          <span style={{ ...mono, fontSize: 20, fontWeight: 600,
                         color: STATE_COLOR[th.direction] }}>{th.direction}</span>
          <Chip text={th.basis} />
          <Line color="var(--ink-3)">as of {th.timestamp}</Line>
        </div>
        <Line color="var(--ink)">{th.summary}</Line>
        <Line color="var(--ink-3)">No confidence number — {th.confidence_note}. Not a signal:
          it cannot authorise or size a trade.</Line>

        <Label>Quality scorecard</Label>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 6 }}>
          {Object.entries(th.quality_scorecard || {}).map(([k, v]) => (
            <div key={k} title={v.basis || ''}>
              <Line color="var(--ink-3)">{k.replaceAll('_', ' ')}</Line>
              <Chip text={v.state} />
            </div>
          ))}
        </div>

        <Label>Evidence groups</Label>
        <Line>{th.agreement_statement}</Line>
        {dep?.state === 'MEASURED' && (
          <Line color="var(--ink-3)">Price indicators measured over {dep.n_days} days: mean
            pairwise correlation {dep.mean_pairwise_corr}, ≈ {dep.effective_independent_signals} effective
            independent signals.</Line>
        )}
        {Object.entries(th.independent_evidence_groups || {}).map(([g, v]) => (
          <div key={g} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '2px 0' }}>
            <span style={{ minWidth: 120 }}><Chip text={g} /></span>
            <Chip text={v.state} /> <Chip text={v.strength} />
            <Line color="var(--ink-3)">{v.bullish_items} bull · {v.bearish_items} bear ·
              {' '}{v.non_directional_items} context · roots {v.roots?.length}</Line>
          </div>
        ))}

        <Label>Supporting</Label>
        <EvidenceList items={th.supporting_evidence} empty="none — no direction is held" />
        <Label>Contradicting</Label>
        <EvidenceList items={th.contradicting_evidence} empty="none recorded" />

        <Label>What explains the move (21 days)</Label>
        {attr?.status === 'MEASURED' ? <>
          <Line color="var(--ink)">{attr.sentence}</Line>
          <Line color="var(--ink-3)">
            {Object.entries(attr.contributions || {}).map(([f, c]) =>
              `${f} ${pct(c.contribution)} (β ${c.beta})`).join(' · ')} · R² {attr.r2} ·
            betas from {attr.estimation_window?.join(' → ')}
          </Line>
        </> : <Line color="var(--ink-3)">{comps.factors?.missing || 'Insufficient data'}</Line>}

        <Label>Cross-asset (association, not cause)</Label>
        {Object.keys(pairs).length ? Object.entries(pairs).map(([p, v]) => (
          <div key={p} style={{ display: 'flex', gap: 8, padding: '1px 0' }}>
            <span style={{ ...mono, fontSize: 12, color: 'var(--ink)', minWidth: 60 }}>{p}</span>
            <Chip text={v.state} />
            <Line color="var(--ink-3)">{v.role} · corr {v.corr_recent ?? '—'} recent vs
              {' '}{v.corr_long ?? '—'} long-run</Line>
          </div>
        )) : <Line color="var(--ink-3)">{comps.cross_asset?.missing || 'Not applicable'}</Line>}

        <Label>Events</Label>
        <div><Chip text={ev.event_risk?.state} /><Line>{ev.event_risk?.basis}</Line></div>
        {(ev.upcoming || []).map(e => (
          <Line key={e.event_id}>{e.kind} {e.event_time?.slice(0, 10)} · {e.importance} ·
            known from {e.known_at_rule}</Line>
        ))}

        <Label>News — one row per story</Label>
        {news.story_clusters?.length ? <>
          <Line color="var(--ink-3)">{news.items} items → {news.clusters} stories
            ({news.syndicated_copies_collapsed} syndicated copies collapsed, {news.excluded_weak_association}
            {' '}not about this instrument). Direction is a keyword lexicon, not a model.</Line>
          {news.story_clusters.slice(0, 6).map(c => (
            <Line key={c.cluster_id}>
              <Chip text={c.freshness} /><Chip text={c.story_novelty} />
              {c.headline} <span style={{ color: 'var(--ink-3)' }}>· {c.event_type} · {c.direction}
              {' '}· {c.publishers} publisher(s)</span>
            </Line>
          ))}
        </> : <Line color="var(--ink-3)">{comps.news?.missing || comps.news?.freshness || 'No data'}</Line>}

        <Label>Historical analogues</Label>
        {th.historical_context?.status === 'MEASURED' ? (
          <Line>{th.historical_context.analogue_count} non-overlapping analogues: win rate
            {' '}{pct(th.historical_context.win_rate)} vs base rate {pct(th.historical_context.base_rate_up)},
            {' '}median forward {pct(th.historical_context.forward_returns?.median)} —
            {' '}<Chip text={th.historical_context.predictive_value} /></Line>
        ) : <Line color="var(--ink-3)">{th.historical_context?.status || 'Analogue insufficient data'}
          {th.historical_context?.why ? ` — ${th.historical_context.why}` : ''}</Line>}

        <Label>Recorded model calls and routing</Label>
        {(th.models || []).length ? th.models.map(m => (
          <Line key={m.model_id}>{m.model_id}: {m.direction} · {(m.labels || []).join(' · ')}</Line>
        )) : <Line color="var(--ink-3)">no unexpired model call recorded for this instrument</Line>}
        <Line color="var(--ink-3)">routing: {th.routing?.summary || th.routing?.reason_code}</Line>

        <Label>Invalidators</Label>
        {(th.invalidators || []).map((v, k) => (
          <Line key={k}><Chip text={v.kind} />{v.condition} — {v.why}</Line>
        ))}

        <Label>Data</Label>
        {Object.entries(comps).map(([k, v]) => (
          <span key={k} title={v.missing || v.source} style={{ marginRight: 8 }}>
            <Line color="var(--ink-3)">{k} <Chip text={v.freshness} /></Line>
          </span>
        ))}
      </>}
    </div>
  )
}
