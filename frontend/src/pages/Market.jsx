/**
 * pages/Market.jsx — ONE market intelligence workspace.
 *
 * This replaces pages/Markets.jsx, which was a TabBar over Signals, Macro,
 * Futures and Options — the exact shape §5 rules out. Those four were never
 * four questions; they were four slices of one question, and making them tabs
 * meant the answer to "what is the market doing?" was assembled in the reader's
 * head rather than by ARIA.
 *
 * The page is one downward read, in the order §5 asks for:
 *
 *      Current market state    regime, breadth, volatility, macro, positioning
 *      What changed            field-level diffs, not prose
 *      Important signals       the extremes of the book
 *      Opportunities / risks   drawn from the same signal set, split by side
 *      Derivatives             futures and options as context, not a tab
 *      ARIA's interpretation   the brief, in its own words
 *
 * Every block is fed by the world model, so a stale source says it is stale
 * here instead of being rendered as today's number.
 */
import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { useFutures, useOptions, useRegime, useSignals, useWorld, useWorldChanges }
  from '../hooks/useApi'
import { ActionBadge, Empty, ScoreBadge, SectionHeader, Spinner } from '../components/UI'
import LiveNews from '../components/LiveNews'

const mono = { fontFamily: 'var(--mono)' }

const Cell = ({ label, value, sub, color = '#fff', warn }) => (
  <div className="bb-card" style={{ padding: '10px 13px' }}>
    <div style={{ ...mono, fontSize: 9, letterSpacing: '.14em', color: 'var(--muted)' }}>{label}</div>
    <div style={{ ...mono, fontSize: 19, fontWeight: 800, color, marginTop: 3 }}>{value ?? '—'}</div>
    {sub && <div style={{ ...mono, fontSize: 9, color: warn ? 'var(--yellow)' : 'var(--muted)', marginTop: 2 }}>{sub}</div>}
  </div>
)

/* A ticker row that always deep-links into research — every symbol anywhere in
   ARIA goes to the same dossier, which is what stops research from becoming a
   separate destination you have to remember to visit. */
function Row({ s }) {
  return (
    <Link to={`/research?symbol=${encodeURIComponent(s.ticker)}`}
          style={{ textDecoration: 'none' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '5px 2px',
        borderBottom: '1px solid #141414',
      }}>
        <span style={{ ...mono, fontSize: 11, fontWeight: 800, color: '#fff', minWidth: 80 }}>
          {s.ticker}
        </span>
        <span style={{ fontSize: 10, color: 'var(--muted)', flex: 1, overflow: 'hidden',
                       textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {s.name}
        </span>
        <ActionBadge action={s.action} />
        <ScoreBadge score={s.composite_score} />
      </div>
    </Link>
  )
}

export default function Market() {
  const { data: world } = useWorld()
  const { data: changes } = useWorldChanges(24)
  const { data: sigData, loading, error } = useSignals()
  const { data: futures } = useFutures()
  const { data: options } = useOptions()
  const [search, setSearch] = useState('')
  const [side, setSide] = useState('all')

  if (loading && !sigData) return <Spinner />

  const m = world?.market
  const all = Object.values(sigData?.signals || {})
  const ranked = [...all].sort((a, b) => (b.composite_score || 0) - (a.composite_score || 0))
  const opportunities = ranked.filter(s => (s.composite_score || 0) > 25).slice(0, 12)
  const risks = ranked.filter(s => (s.composite_score || 0) < -25).slice(-12).reverse()

  const filtered = ranked.filter(s => {
    const okSide = side === 'all' ? true
      : side === 'bull' ? s.composite_score > 10
      : side === 'bear' ? s.composite_score < -10
      : Math.abs(s.composite_score) <= 10
    const q = search.trim().toLowerCase()
    const okSearch = !q || s.ticker?.toLowerCase().includes(q) || (s.name || '').toLowerCase().includes(q)
    return okSide && okSearch
  })

  const futureRows = Object.entries(futures || {})
    .sort((a, b) => (b[1].confirmation_score || 0) - (a[1].confirmation_score || 0))
    .slice(0, 8)
  const optionRows = Object.entries(options || {}).slice(0, 8)

  return (
    <div style={{ maxWidth: 1240, margin: '0 auto' }}>
      <div style={{ marginBottom: 12 }}>
        <div style={{ ...mono, fontSize: 16, fontWeight: 800, color: 'var(--orange)', letterSpacing: '.12em' }}>
          ∿ MARKET
        </div>
        <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
          Equities, macro, futures, options and volatility as one state — not four tabs.
        </div>
      </div>

      {/* ── CURRENT MARKET STATE ── */}
      <SectionHeader>CURRENT MARKET STATE</SectionHeader>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))',
                    gap: 10, marginBottom: 8 }}>
        {/* The regime comes from the classifier itself (/api/regime), which
            reports how much of its answer it actually observed. The world
            model's cached string is the fallback. `confidence` here is INPUT
            COVERAGE — a regime carried by two of four observed inputs is still
            the best available reading, but it must not look like one carried
            by all four. */}
        <Cell label="REGIME" value={regime?.regime || m?.regime?.macro_model || '—'}
              color="var(--orange)"
              warn={regime?.stale || m?.regime?.stale}
              sub={regime?.stale ? `from macro data ${regime.age_days}d old`
                   : regime?.coverage != null
                     ? `${(regime.coverage * 100).toFixed(0)}% input coverage`
                     : (m?.regime?.agree ? 'brain agrees' : 'brain disagrees')} />
        <Cell label="BREADTH" value={m ? `${m.breadth.advancing_pct}%` : '—'}
              color={(m?.breadth?.net_tilt || 0) >= 0 ? 'var(--green)' : 'var(--red)'}
              sub={m ? `${m.breadth.bullish}↑ / ${m.breadth.bearish}↓ of ${m.breadth.tracked}` : null} />
        <Cell label="VOLATILITY" value={m?.volatility?.vix ?? '—'} color="var(--blue)"
              warn={m?.volatility?.vix_stale}
              sub={m?.volatility?.vix_stale ? 'VIX is stale'
                   : `median realised ${m?.volatility?.median_realised_vol != null
                        ? (m.volatility.median_realised_vol * 100).toFixed(0) + '%' : '—'}`} />
        <Cell label="MACRO SCORE" value={m?.macro?.score ?? '—'}
              color={(m?.macro?.score || 0) > 0 ? 'var(--green)' : 'var(--red)'}
              warn={m?.macro?.stale} sub={m?.macro?.why || `10Y ${m?.macro?.treasury_10y ?? '—'}`} />
        <Cell label="YIELD SPREAD" value={m?.macro?.yield_spread_10y2y ?? '—'}
              sub="10Y − 2Y" />
        <Cell label="POSITIONING" value={world?.portfolio?.open_positions ?? '—'}
              color="var(--orange)" sub="open desk positions" />
      </div>

      {/* Staleness stated once, loudly, rather than repeated under every tile. */}
      {!!world?.stale_blocks?.length && (
        <div className="bb-card" style={{ borderColor: 'rgba(255,179,36,.35)', marginBottom: 14 }}>
          <div style={{ ...mono, fontSize: 10, color: 'var(--yellow)', lineHeight: 1.6 }}>
            ⚠ Some of this is not current: {world.stale_blocks.join(', ')}. The
            numbers above are the last completed run, not this moment.
          </div>
        </div>
      )}

      {/* ── WHAT CHANGED ── */}
      <SectionHeader>WHAT CHANGED · LAST 24H</SectionHeader>
      <div className="bb-card" style={{ marginBottom: 14 }}>
        {!(changes?.changed || []).length && (
          <div style={{ ...mono, fontSize: 10, color: 'var(--muted)' }}>
            {changes?.note || 'Nothing material moved in the window.'}
          </div>
        )}
        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
          {(changes?.changed || []).map((c, i) => (
            <div key={i} style={{ ...mono, fontSize: 11 }}>
              <span style={{ color: 'var(--muted)' }}>{c.field}: </span>
              <span style={{ color: 'var(--red)' }}>{String(c.from)}</span>
              <span style={{ color: 'var(--muted)' }}> → </span>
              <span style={{ color: 'var(--green)', fontWeight: 700 }}>{String(c.to)}</span>
            </div>
          ))}
        </div>
        {!!(changes?.notable_events || []).length && (
          <div style={{ marginTop: 10, borderTop: '1px solid #141414', paddingTop: 8 }}>
            {changes.notable_events.slice(0, 5).map((e, i) => (
              <div key={i} style={{ ...mono, fontSize: 10, color: 'var(--text-dim)', padding: '2px 0' }}>
                <span style={{ color: 'var(--orange)' }}>{e.kind}</span> — {e.summary}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── OPPORTUNITIES / RISKS ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
        <div className="bb-card">
          <div className="bb-card-header" style={{ color: 'var(--green)' }}>OPPORTUNITIES</div>
          {!opportunities.length && <Empty message="No signal above +25 in the book." />}
          {opportunities.map(s => <Row key={s.ticker} s={s} />)}
        </div>
        <div className="bb-card">
          <div className="bb-card-header" style={{ color: 'var(--red)' }}>RISKS</div>
          {!risks.length && <Empty message="No signal below −25 in the book." />}
          {risks.map(s => <Row key={s.ticker} s={s} />)}
        </div>
      </div>

      {/* ── THE REGIME, WITH ITS EVIDENCE ──
          A regime label with no evidence under it is a mood. These are the
          four threshold tests the classifier actually ran, and whether each
          input was observed or fell back to a default. */}
      {regime && (
        <div className="bb-card" style={{ marginBottom: 14 }}>
          <div className="bb-card-header" style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <span>REGIME · WHY</span>
            <span style={{ ...mono, fontSize: 9, color: 'var(--muted)' }}>
              {regime.as_of ? `AS OF ${regime.as_of}` : ''}
            </span>
          </div>
          <div style={{ ...mono, fontSize: 11, color: 'var(--text-dim)', marginBottom: 8 }}>
            {regime.meaning}
          </div>
          {(regime.evidence || []).map((e, i) => (
            <div key={i} style={{ ...mono, fontSize: 10, color: 'var(--text-dim)',
              lineHeight: 1.7 }}>
              <span style={{ color: 'var(--orange)', marginRight: 7 }}>→</span>{e}
            </div>
          ))}
          {!!(regime.would_flip_to || []).length && (
            <div style={{ marginTop: 9, paddingTop: 7, borderTop: '1px solid #141414' }}>
              <div style={{ ...mono, fontSize: 8, letterSpacing: '.2em',
                color: 'var(--muted)', marginBottom: 4 }}>WOULD CHANGE IF</div>
              {regime.would_flip_to.map((f, i) => (
                <div key={i} style={{ ...mono, fontSize: 9, color: 'var(--muted)' }}>
                  {f.if} — {f.distance}{f.unit} away
                </div>
              ))}
            </div>
          )}
          {(regime.caveats || []).map((c, i) => (
            <div key={i} style={{ ...mono, fontSize: 9, color: 'var(--yellow)',
              lineHeight: 1.6, marginTop: 7 }}>△ {c}</div>
          ))}
        </div>
      )}

      {/* ── LIVE NEWS — continuous, and deliberately NOT the daily report ──
          The daily report is the slow curated product and has its own
          destination. This is the world arriving. */}
      <SectionHeader>LIVE WORLD FEED</SectionHeader>
      <LiveNews limit={25} hours={24} />

      {/* ── DERIVATIVES — context, not a destination ── */}
      <SectionHeader>DERIVATIVES</SectionHeader>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
        <div className="bb-card">
          <div className="bb-card-header">FUTURES · CONFIRMATION</div>
          {!futureRows.length && <Empty message="No futures data in the last run." />}
          {futureRows.map(([ticker, f]) => (
            <div key={ticker} style={{ display: 'flex', alignItems: 'center', gap: 10,
                                       padding: '4px 0', borderBottom: '1px solid #141414' }}>
              <span style={{ ...mono, fontSize: 11, fontWeight: 700, color: '#fff', minWidth: 74 }}>{ticker}</span>
              <span style={{ fontSize: 10, color: 'var(--muted)', flex: 1 }}>{f.name}</span>
              <ScoreBadge score={f.confirmation_score} />
            </div>
          ))}
        </div>
        <div className="bb-card">
          <div className="bb-card-header">OPTIONS · POSITIONING</div>
          {!optionRows.length && <Empty message="No options data in the last run." />}
          {/* Fields match data/options_data.json exactly. The first pass here
              guessed `iv`, `put_call_ratio` and `signal`, none of which exist
              in that file, so every row rendered as a bare ticker with three
              silently-empty columns beside it — the quiet kind of wrong that
              looks like "no data" rather than a bug. */}
          {optionRows.map(([ticker, o]) => (
            <div key={ticker} style={{ display: 'flex', alignItems: 'center', gap: 10,
                                       padding: '4px 0', borderBottom: '1px solid #141414' }}>
              <span style={{ ...mono, fontSize: 11, fontWeight: 700, color: '#fff', minWidth: 74 }}>{ticker}</span>
              <span style={{ ...mono, fontSize: 10, color: 'var(--muted)', flex: 1 }}>
                {o.avg_call_iv != null ? `IV ${(o.avg_call_iv * 100).toFixed(0)}%` : ''}
                {o.pc_oi_ratio != null ? ` · P/C OI ${Number(o.pc_oi_ratio).toFixed(2)}` : ''}
                {o.iv_skew != null ? ` · skew ${(o.iv_skew * 100).toFixed(1)}` : ''}
              </span>
              {o.final_sentiment_score != null && (
                <ScoreBadge score={o.final_sentiment_score} />
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ── THE BOOK ── */}
      <SectionHeader>THE BOOK · {filtered.length} OF {all.length}</SectionHeader>
      <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <input value={search} onChange={e => setSearch(e.target.value)}
               placeholder="Filter ticker or name…" spellCheck={false}
               style={{ ...mono, background: '#0d0d0d', border: '1px solid var(--border-2)',
                        color: 'var(--text)', fontSize: 12, padding: '6px 11px',
                        borderRadius: 4, outline: 'none', flex: '1 1 200px' }} />
        {['all', 'bull', 'bear', 'flat'].map(f => (
          <button key={f} onClick={() => setSide(f)}
            style={{ ...mono, fontSize: 10, letterSpacing: '.1em', padding: '6px 12px',
                     borderRadius: 4, cursor: 'pointer',
                     background: side === f ? 'rgba(255,36,71,.14)' : '#0d0d0d',
                     border: `1px solid ${side === f ? 'var(--orange)' : 'var(--border-2)'}`,
                     color: side === f ? 'var(--orange)' : 'var(--text-dim)' }}>
            {f.toUpperCase()}
          </button>
        ))}
      </div>
      <div className="bb-card" style={{ maxHeight: 520, overflowY: 'auto' }}>
        {error && <Empty message={`Signals unavailable — ${error}`} />}
        {!error && !filtered.length && <Empty message="Nothing matches that filter." />}
        {filtered.slice(0, 200).map(s => <Row key={s.ticker} s={s} />)}
      </div>

      {/* ── ARIA'S INTERPRETATION ── */}
      {world?.market && (
        <div className="bb-card" style={{ marginTop: 14 }}>
          <div className="bb-card-header">ARIA’S INTERPRETATION</div>
          <div style={{ ...mono, fontSize: 11, color: 'var(--text-dim)', lineHeight: 1.75 }}>
            Leading: {(m.leaders || []).map(l => l.ticker).filter(Boolean).join(', ') || '—'}.
            {' '}Lagging: {(m.laggards || []).map(l => l.ticker).filter(Boolean).join(', ') || '—'}.
            {' '}The brain is focused on {(m.brain_focus || []).join(', ') || 'nothing in particular'}.
            {!!world.unknowns?.length && (
              <>
                {' '}Caveats I would attach to all of the above:{' '}
                <span style={{ color: 'var(--yellow)' }}>{world.unknowns[0]}</span>
              </>
            )}
          </div>
          <Link to="/" style={{ ...mono, fontSize: 9, color: 'var(--orange)', textDecoration: 'none',
                                display: 'block', marginTop: 10, letterSpacing: '.15em' }}>
            ASK ARIA ABOUT THIS →
          </Link>
        </div>
      )}
    </div>
  )
}
