/**
 * components/LiveNews.jsx
 *
 * The continuous world feed. NOT the daily report.
 *
 * The two were mixed, and they answer different questions:
 *
 *      DAILY REPORT   what happened today, curated once, kept forever
 *      LIVE NEWS      what is arriving right now, event-driven
 *
 * This polls with a CURSOR (`next_since`), so it asks "anything since X?" and
 * receives an empty list most of the time. That is the correct answer and it is
 * rendered as silence — a feed that must produce a row per tick will produce
 * noise, and noise in an intelligence feed is worse than nothing because it
 * looks like information.
 *
 * Two independent axes travel with every row and are shown separately:
 *
 *      EVIDENCE STATE   OBSERVATION vs VERIFIED — a headline is not a fact
 *      SOURCE TIER      where on the evidence ladder it came from
 *
 * A rumour on a high-tier feed is still a rumour; a dull regulatory filing is
 * the highest tier there is. Collapsing them into one "credibility" number is
 * how a Reddit post ends up looking like an 8-K.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { fetchLiveNews, useNewsSources } from '../hooks/useApi'

const mono = { fontFamily: 'var(--font)' }

const TIER_COLOR = {
  primary_filing: 'var(--green)',
  official_data: 'var(--green)',
  company_release: 'var(--blue)',
  academic: 'var(--blue)',
  quality_press: 'var(--ink-2)',
  aggregator: 'var(--ink-3)',
  secondary: 'var(--ink-3)',
  social: 'var(--yellow)',
  anonymous: 'var(--accent)',
  unknown: 'var(--ink-3)',
}

const CLAIM_COLOR = {
  FACT: 'var(--green)', INFERENCE: 'var(--blue)', HYPOTHESIS: 'var(--blue)',
  CLAIM: 'var(--yellow)', RUMOUR: 'var(--accent)', UNCLASSIFIED: 'var(--ink-3)',
}

function ago(iso) {
  if (!iso) return '—'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return `${Math.max(0, Math.round(s))}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  if (s < 86400) return `${Math.round(s / 3600)}h`
  return `${Math.round(s / 86400)}d`
}

export default function LiveNews({ limit = 25, hours = 24, compact = false,
                                   pollMs = 15000 }) {
  const [items, setItems] = useState([])
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState(null)
  const [lastPoll, setLastPoll] = useState(null)
  const cursor = useRef(null)
  const seeded = useRef(false)
  // What is actually feeding this feed. A stream carrying one regulatory
  // filing and 37 anonymous posts is a different thing from one carrying
  // Reuters, and the reader is entitled to know which they have rather than
  // inferring it from whatever happens to be on screen.
  const { data: sources } = useNewsSources()

  const poll = useCallback(async () => {
    try {
      // First call takes the window; every later call asks only for what is
      // NEW since the cursor the server handed back.
      const since = seeded.current ? cursor.current : null
      const d = await fetchLiveNews(since, seeded.current ? hours : hours, limit)
      setError(null)
      setMeta(d)
      setLastPoll(Date.now())
      if (d.next_since) cursor.current = d.next_since
      if (!seeded.current) {
        setItems(d.items || [])
        seeded.current = true
      } else if (d.items?.length) {
        // Prepend genuinely new rows; never re-add one already on screen.
        setItems(prev => {
          const have = new Set(prev.map(i => i.id))
          const fresh = d.items.filter(i => !have.has(i.id))
                               .map(i => ({ ...i, _new: true }))
          return [...fresh, ...prev].slice(0, limit * 2)
        })
      }
    } catch (e) {
      setError(e.message)
    }
  }, [hours, limit])

  useEffect(() => {
    poll()
    const id = setInterval(poll, pollMs)
    return () => clearInterval(id)
  }, [poll, pollMs])

  return (
    <div className="bb-card" style={{ marginBottom: 14 }}>
      <div className="bb-card-header" style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span>Live news · world feed</span>
        <span style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', }}>
          {lastPoll ? `Checked ${ago(new Date(lastPoll).toISOString())} Ago` : 'Checking…'}
        </span>
      </div>

      {error && (
        <div style={{ ...mono, fontSize: 12, color: 'var(--red)' }}>{error}</div>
      )}

      {!error && !items.length && (
        <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.7 }}>
          Nothing new in this window. Only developments ARIA has actually
          observed appear here.
        </div>
      )}

      <div style={{ maxHeight: compact ? 300 : 560, overflowY: 'auto' }}>
        {items.map((n, i) => (
          <div key={n.id || i} style={{
            padding: '6px 0', borderBottom: '1px solid var(--inset)',
            background: n._new ? 'color-mix(in srgb, var(--green) 6%, transparent)' : 'transparent',
          }}>
            <div style={{ display: 'flex', gap: 7, alignItems: 'baseline', flexWrap: 'wrap' }}>
              <span style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', minWidth: 30 }}>
                {ago(n.at)}
              </span>
              {/* Two axes, two badges. Deliberately not merged. */}
              <span title="Is this evidence, or something somebody said?"
                style={{
                  ...mono, fontSize: 11, fontWeight: 600, 
                  padding: '0 4px', borderRadius: 8,
                  color: n.evidence_state === 'VERIFIED' ? 'var(--green)' : 'var(--ink-3)',
                  border: `1px solid ${n.evidence_state === 'VERIFIED' ? 'var(--green)' : 'var(--line)'}`,
                }}>
                {n.evidence_state}
              </span>
              <span title={`claim type${n.claim_confidence != null
                ? ` · classifier confidence ${(n.claim_confidence * 100).toFixed(0)}%` : ''}`}
                style={{
                  ...mono, fontSize: 11, fontWeight: 600, 
                  color: CLAIM_COLOR[n.claim_type] || 'var(--ink-3)',
                }}>
                {n.claim_type}
              </span>
              <a href={n.url} target="_blank" rel="noreferrer" style={{
                ...mono, fontSize: 13, color: 'var(--ink)', textDecoration: 'none',
                flex: 1, minWidth: 220,
              }}>
                {n.headline}
              </a>
            </div>
            <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', marginTop: 2,
              paddingLeft: 37 }}>
              <span style={{ color: TIER_COLOR[n.source_tier] || 'var(--ink-3)' }}>
                {String(n.source_tier).replace(/_/g, ' ')}
              </span>
              {' · '}{n.source}
              {/* The instrument, and how we know the story is about it.
                  103 of 197 stored observations were the ticker matching as an
                  ordinary English word — Bill Ackman under `BILL`. Those are
                  held back now, and the ones that survive say why. */}
              {n.asset && (
                <>
                  {' · '}
                  <span style={{ color: 'var(--ink-2)', fontWeight: 600 }}>{n.asset}</span>
                  {n.association === 'NAMED' && (
                    <span title="the headline names the company rather than the ticker">
                      {' '}(by name)
                    </span>
                  )}
                </>
              )}
              {n.corroboration > 1 && (
                <span style={{ color: 'var(--green)' }}>
                  {' · '}corroborated ×{n.corroboration}
                </span>
              )}
              {n.salience != null && ` · salience ${n.salience}`}
            </div>
          </div>
        ))}
      </div>

      {meta && (
        <div style={{ ...mono, fontSize: 11, color: 'var(--ink-3)', marginTop: 9,
          lineHeight: 1.6 }}>
          {meta.raw_observations} observation(s) in window ·
          {' '}{meta.deduplicated_away} collapsed as the same story ·
          {' '}{meta.filtered_out} held back as unusable or not about the instrument
          {meta.filtered_out > 0 && (
            <> ({Object.entries(meta.filter_reasons || {})
                  .map(([w, n]) => `${n}× ${w}`).join(', ')})</>
          )}
          <br />OBSERVATION means the eye saw a claim, not that the claim is true.
          {sources?.by_tier && (
            <>
              <br />Sources in the last 24h:{' '}
              {Object.entries(sources.by_tier)
                .sort((a, b) => b[1] - a[1])
                .map(([tier, n]) => `${n} ${tier.replace(/_/g, ' ')}`)
                .join(' · ')}
            </>
          )}
        </div>
      )}
    </div>
  )
}
