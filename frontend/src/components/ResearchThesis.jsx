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

const MONO = 'var(--mono)'
const mono = { fontFamily: MONO }

const STATE_COLOR = {
  BULLISH: 'var(--green)', BEARISH: 'var(--red)', NEUTRAL: 'var(--muted)',
  VALID: 'var(--green)', FRESH: 'var(--green)', ALIGNED: 'var(--green)', LOW: 'var(--green)',
  STRONG: 'var(--green)', MODERATE: 'var(--yellow)', WEAK: 'var(--muted)',
  LIMITED: 'var(--yellow)', NARROW: 'var(--yellow)', CONCENTRATED: 'var(--yellow)',
  ELEVATED: 'var(--yellow)', STALE: 'var(--yellow)', LIVE_ONLY: 'var(--yellow)',
  SINGLE_GROUP: 'var(--yellow)', DEGRADED: 'var(--yellow)',
  HIGH: 'var(--red)', CONFLICTED: 'var(--red)', MISSING: 'var(--red)', VIOLATED: 'var(--red)',
}

const Chip = ({ text }) => {
  const c = STATE_COLOR[text] || 'var(--muted)'
  return (
    <span style={{ ...mono, fontSize: 9, letterSpacing: '.06em', color: c, border: `1px solid ${c}`,
                   borderRadius: 3, padding: '1px 5px', marginRight: 5, whiteSpace: 'nowrap' }}>
      {String(text ?? 'UNKNOWN').replaceAll('_', ' ')}
    </span>
  )
}

const Label = ({ children }) => (
  <div style={{ ...mono, fontSize: 9, letterSpacing: '.14em', color: 'var(--orange)',
                fontWeight: 800, margin: '12px 0 5px' }}>{children}</div>
)

const Line = ({ children, color = 'var(--text-dim)' }) => (
  <div style={{ ...mono, fontSize: 10, color, lineHeight: 1.6 }}>{children}</div>
)

const pct = x => (x == null ? '—' : `${(x * 100).toFixed(1)}%`)

function EvidenceList({ items, empty }) {
  if (!items?.length) return <Line color="var(--muted)">{empty}</Line>
  return items.slice(0, 10).map(i => (
    <div key={i.item_id} style={{ display: 'flex', gap: 8, padding: '2px 0' }}>
      <span style={{ minWidth: 104 }}><Chip text={i.group} /></span>
      <Line>
        <span style={{ color: '#fff' }}>{i.label}</span> — {i.explanation}
        <span style={{ color: 'var(--muted)' }}> · {i.freshness} · {i.reliability?.state}</span>
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
      <div className="bb-card-header">RESEARCH THESIS — INDEPENDENT EVIDENCE</div>
      {state === 'idle' && (
        <div style={{ padding: '10px 2px' }}>
          <button onClick={run} style={{
            ...mono, fontSize: 11, fontWeight: 700, padding: '7px 16px', cursor: 'pointer',
            background: 'none', border: '1px solid var(--orange)', color: 'var(--orange)', borderRadius: 4,
          }}>BUILD RESEARCH THESIS FOR {symbol}</button>
          <div style={{ marginTop: 7 }}>
            <Line color="var(--muted)">
              Assembles point-in-time evidence (price, market and factors, fundamentals,
              macro, news stories, cross-asset, events, recorded model calls), groups it by
              what it is derived from, and explains the view. Fetches market data; may take
              a while the first time.
            </Line>
          </div>
        </div>
      )}
      {state === 'loading' && <Line>assembling evidence…</Line>}
      {state === 'denied' && <Line color="var(--muted)">The research thesis reads the owner&apos;s
        prediction record. Owner sign-in required.</Line>}
      {state === 'error' && <Line color="var(--red)">{err}</Line>}

      {state === 'ready' && th && <>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 4 }}>
          <span style={{ ...mono, fontSize: 18, fontWeight: 800,
                         color: STATE_COLOR[th.direction] }}>{th.direction}</span>
          <Chip text={th.basis} />
          <Line color="var(--muted)">as of {th.timestamp}</Line>
        </div>
        <Line color="#fff">{th.summary}</Line>
        <Line color="var(--muted)">No confidence number — {th.confidence_note}. Not a signal:
          it cannot authorise or size a trade.</Line>

        <Label>QUALITY SCORECARD</Label>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 6 }}>
          {Object.entries(th.quality_scorecard || {}).map(([k, v]) => (
            <div key={k} title={v.basis || ''}>
              <Line color="var(--muted)">{k.replaceAll('_', ' ')}</Line>
              <Chip text={v.state} />
            </div>
          ))}
        </div>

        <Label>EVIDENCE GROUPS</Label>
        <Line>{th.agreement_statement}</Line>
        {dep?.state === 'MEASURED' && (
          <Line color="var(--muted)">Price indicators measured over {dep.n_days} days: mean
            pairwise correlation {dep.mean_pairwise_corr}, ≈ {dep.effective_independent_signals} effective
            independent signals.</Line>
        )}
        {Object.entries(th.independent_evidence_groups || {}).map(([g, v]) => (
          <div key={g} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '2px 0' }}>
            <span style={{ minWidth: 120 }}><Chip text={g} /></span>
            <Chip text={v.state} /> <Chip text={v.strength} />
            <Line color="var(--muted)">{v.bullish_items} bull · {v.bearish_items} bear ·
              {' '}{v.non_directional_items} context · roots {v.roots?.length}</Line>
          </div>
        ))}

        <Label>SUPPORTING</Label>
        <EvidenceList items={th.supporting_evidence} empty="none — no direction is held" />
        <Label>CONTRADICTING</Label>
        <EvidenceList items={th.contradicting_evidence} empty="none recorded" />

        <Label>WHAT EXPLAINS THE MOVE (21 DAYS)</Label>
        {attr?.status === 'MEASURED' ? <>
          <Line color="#fff">{attr.sentence}</Line>
          <Line color="var(--muted)">
            {Object.entries(attr.contributions || {}).map(([f, c]) =>
              `${f} ${pct(c.contribution)} (β ${c.beta})`).join(' · ')} · R² {attr.r2} ·
            betas from {attr.estimation_window?.join(' → ')}
          </Line>
        </> : <Line color="var(--muted)">{comps.factors?.missing || 'INSUFFICIENT DATA'}</Line>}

        <Label>CROSS-ASSET (ASSOCIATION, NOT CAUSE)</Label>
        {Object.keys(pairs).length ? Object.entries(pairs).map(([p, v]) => (
          <div key={p} style={{ display: 'flex', gap: 8, padding: '1px 0' }}>
            <span style={{ ...mono, fontSize: 10, color: '#fff', minWidth: 60 }}>{p}</span>
            <Chip text={v.state} />
            <Line color="var(--muted)">{v.role} · corr {v.corr_recent ?? '—'} recent vs
              {' '}{v.corr_long ?? '—'} long-run</Line>
          </div>
        )) : <Line color="var(--muted)">{comps.cross_asset?.missing || 'NOT APPLICABLE'}</Line>}

        <Label>EVENTS</Label>
        <div><Chip text={ev.event_risk?.state} /><Line>{ev.event_risk?.basis}</Line></div>
        {(ev.upcoming || []).map(e => (
          <Line key={e.event_id}>{e.kind} {e.event_time?.slice(0, 10)} · {e.importance} ·
            known from {e.known_at_rule}</Line>
        ))}

        <Label>NEWS — ONE ROW PER STORY</Label>
        {news.story_clusters?.length ? <>
          <Line color="var(--muted)">{news.items} items → {news.clusters} stories
            ({news.syndicated_copies_collapsed} syndicated copies collapsed, {news.excluded_weak_association}
            {' '}not about this instrument). Direction is a keyword lexicon, not a model.</Line>
          {news.story_clusters.slice(0, 6).map(c => (
            <Line key={c.cluster_id}>
              <Chip text={c.freshness} /><Chip text={c.story_novelty} />
              {c.headline} <span style={{ color: 'var(--muted)' }}>· {c.event_type} · {c.direction}
              {' '}· {c.publishers} publisher(s)</span>
            </Line>
          ))}
        </> : <Line color="var(--muted)">{comps.news?.missing || comps.news?.freshness || 'NO DATA'}</Line>}

        <Label>HISTORICAL ANALOGUES</Label>
        {th.historical_context?.status === 'MEASURED' ? (
          <Line>{th.historical_context.analogue_count} non-overlapping analogues: win rate
            {' '}{pct(th.historical_context.win_rate)} vs base rate {pct(th.historical_context.base_rate_up)},
            {' '}median forward {pct(th.historical_context.forward_returns?.median)} —
            {' '}<Chip text={th.historical_context.predictive_value} /></Line>
        ) : <Line color="var(--muted)">{th.historical_context?.status || 'ANALOGUE INSUFFICIENT DATA'}
          {th.historical_context?.why ? ` — ${th.historical_context.why}` : ''}</Line>}

        <Label>RECORDED MODEL CALLS AND ROUTING</Label>
        {(th.models || []).length ? th.models.map(m => (
          <Line key={m.model_id}>{m.model_id}: {m.direction} · {(m.labels || []).join(' · ')}</Line>
        )) : <Line color="var(--muted)">no unexpired model call recorded for this instrument</Line>}
        <Line color="var(--muted)">routing: {th.routing?.summary || th.routing?.reason_code}</Line>

        <Label>INVALIDATORS</Label>
        {(th.invalidators || []).map((v, k) => (
          <Line key={k}><Chip text={v.kind} />{v.condition} — {v.why}</Line>
        ))}

        <Label>DATA</Label>
        {Object.entries(comps).map(([k, v]) => (
          <span key={k} title={v.missing || v.source} style={{ marginRight: 8 }}>
            <Line color="var(--muted)">{k} <Chip text={v.freshness} /></Line>
          </span>
        ))}
      </>}
    </div>
  )
}
