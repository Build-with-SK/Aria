/**
 * pages/DailyReport.jsx — the daily intelligence product, its own destination.
 *
 * It used to be a card at the bottom of another page, rendering the single
 * `data/daily_report.json` that every run overwrote — so it showed one report,
 * dated three months earlier, under a heading that implied today.
 *
 * Now:
 *   · one report per day, addressed BY DATE, kept forever;
 *   · a day with no report says REPORT NOT GENERATED and stays saying it —
 *     yesterday's conclusions are never shown under today's date;
 *   · older reports are openable, because a record you cannot go back to is
 *     not a record.
 *
 * This is the SLOW product. The live world feed is a different thing and lives
 * in Market and in Brain; mixing them was the complaint.
 */
import React, { useState } from 'react'
import { useDailyReport, useReportHistory, generateDailyReport } from '../hooks/useApi'
import { Spinner } from '../components/UI'
import { DataUnavailable, hasRecord } from '../components/DataState'

const mono = { fontFamily: 'var(--font)' }

const Section = ({ title, children, note }) => (
  <div style={{ marginBottom: 26 }}>
    <div style={{
      ...mono, fontSize: 12, fontWeight: 600, 
      color: 'var(--accent)', paddingBottom: 6, marginBottom: 12,
      borderBottom: '1px solid var(--line)',
    }}>
      {title}
    </div>
    {children}
    {note && (
      <div style={{ ...mono, fontSize: 11, color: 'var(--ink-3)', marginTop: 8, lineHeight: 1.6 }}>
        {note}
      </div>
    )}
  </div>
)

const Stat = ({ label, value, sub, color = 'var(--ink)' }) => (
  <div style={{ minWidth: 96 }}>
    <div style={{ ...mono, fontSize: 11, color: 'var(--ink-3)' }}>{label}</div>
    <div style={{ ...mono, fontSize: 17, fontWeight: 600, color, marginTop: 2 }}>
      {value ?? '—'}
    </div>
    {sub && <div style={{ ...mono, fontSize: 11, color: 'var(--ink-3)' }}>{sub}</div>}
  </div>
)

/** A macro reading that says whether it was OBSERVED or assumed. */
const Reading = ({ row }) => {
  if (!row) return null
  const assumed = row.status === 'ASSUMED'
  return (
    <div style={{ minWidth: 110 }}>
      <div style={{ ...mono, fontSize: 11, color: 'var(--ink-3)' }}>
        {row.label}
      </div>
      <div style={{
        ...mono, fontSize: 17, fontWeight: 600, marginTop: 2,
        color: assumed ? 'var(--ink-3)' : 'var(--ink)',
      }}>
        {row.value ?? '—'}
      </div>
      <div style={{ ...mono, fontSize: 11, color: assumed ? 'var(--yellow)' : 'var(--green)' }}>
        {assumed ? 'ASSUMED — not observed' : 'observed'}
      </div>
    </div>
  )
}

function Bullets({ items, colour = 'var(--ink-2)', marker = '·' }) {
  if (!items?.length) return (
    <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>Nothing recorded.</div>
  )
  return items.map((t, i) => (
    <div key={i} style={{ ...mono, fontSize: 13, color: colour, lineHeight: 1.75, padding: '2px 0' }}>
      <span style={{ color: 'var(--accent)', marginRight: 8 }}>{marker}</span>{t}
    </div>
  ))
}

function MoversTable({ rows, positive }) {
  if (!rows?.length) return (
    <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>No signals available.</div>
  )
  return (
    <div>
      {rows.map((r, i) => (
        <div key={i} style={{
          display: 'flex', gap: 10, alignItems: 'baseline',
          padding: '4px 0', borderBottom: '1px solid var(--inset)',
        }}>
          <span style={{ ...mono, fontSize: 13, fontWeight: 600, color: 'var(--ink)', minWidth: 62 }}>
            {r.ticker}
          </span>
          {/* Identity travels with the price — a bare ticker is a label, and
              this is the report where that distinction was worth $214 vs $2.92. */}
          <span style={{ ...mono, fontSize: 11, color: 'var(--ink-3)', minWidth: 84 }}>
            {r.provider_symbol}{r.price_unit ? ` · ${r.price_unit}` : ''}
          </span>
          <span style={{ ...mono, fontSize: 12, color: 'var(--ink-2)', flex: 1,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {r.name}
          </span>
          <span style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>{r.action}</span>
          <span style={{
            ...mono, fontSize: 13, fontWeight: 600, minWidth: 52, textAlign: 'right',
            color: positive ? 'var(--green)' : 'var(--red)',
          }}>
            {r.score != null && Number.isFinite(Number(r.score)) ? `${r.score > 0 ? '+' : ''}${Number(r.score).toFixed(1)}` : 'withheld'}
          </span>
        </div>
      ))}
    </div>
  )
}

export default function DailyReport() {
  const [date, setDate] = useState(null)      // null = today
  const { data: report, loading, error, reload } = useDailyReport(date)
  const { data: hist, reload: reloadHist } = useReportHistory()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const reports = hist?.reports || []
  const missing = report?.status === 'NOT_GENERATED'

  const generate = async () => {
    setBusy(true); setErr(null)
    try {
      await generateDailyReport(date || undefined)
      reload(); reloadHist()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const macro = report?.macro || {}
  const market = report?.market || {}
  const news = report?.news || {}
  const assess = report?.assessment || {}
  const record = report?.track_record || {}

  return (
    <div style={{ maxWidth: 1180, margin: '0 auto', display: 'grid',
      gridTemplateColumns: 'minmax(0,1fr) 208px', gap: 24 }}
      className="aria-intel-grid">

      <div style={{ minWidth: 0 }}>
        {/* ── masthead ── */}
        <div style={{ marginBottom: 22 }}>
          <div style={{ ...mono, fontSize: 12, color: 'var(--accent)' }}>
            ARIA · daily intelligence
          </div>
          <div style={{ ...mono, fontSize: 34, fontWeight: 600, color: 'var(--ink)', marginTop: 4 }}>
            {report?.date || '—'}
          </div>
          <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', marginTop: 3 }}>
            {report?.generated_at
              ? `Generated ${new Date(report.generated_at).toLocaleString()}`
              : 'Not generated'}
            {report?.market_regime ? ` · ${report.market_regime}` : ''}
            {report?.imported_from ? ' · imported from the earlier archive' : ''}
          </div>
        </div>

        {loading && <Spinner />}
        {!loading && (error || !hasRecord(report)) && <DataUnavailable name="Daily report" error={error} />}

        {missing && (
          <div className="bb-card" style={{ borderColor: 'color-mix(in srgb, var(--yellow) 40%, transparent)' }}>
            <div className="bb-card-header" style={{ color: 'var(--yellow)' }}>
              Report not generated
            </div>
            <div style={{ ...mono, fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.8 }}>
              {report.message}
              <div style={{ color: 'var(--ink-3)', marginTop: 8 }}>{report.note}</div>
              {report.most_recent_available && (
                <div style={{ marginTop: 8 }}>
                  Most recent available:{' '}
                  <button onClick={() => setDate(report.most_recent_available)} style={{
                    ...mono, background: 'none', border: 'none', color: 'var(--accent)',
                    cursor: 'pointer', padding: 0, fontSize: 13, textDecoration: 'underline',
                  }}>{report.most_recent_available}</button>
                </div>
              )}
            </div>
            <button onClick={generate} disabled={busy} style={{
              ...mono, marginTop: 14, fontSize: 12, fontWeight: 600, 
              padding: '8px 16px', borderRadius: 8, cursor: busy ? 'wait' : 'pointer',
              background: 'var(--accent)', border: 'none', color: 'var(--on-solid)',
            }}>{busy ? 'Generating…' : 'Generate this report'}</button>
            {err && <div style={{ ...mono, fontSize: 12, color: 'var(--red)', marginTop: 8 }}>{err}</div>}
          </div>
        )}

        {!missing && hasRecord(report) && !error && (
          <>
            <Section title="Executive summary">
              <div style={{ ...mono, fontSize: 14, color: 'var(--ink)', lineHeight: 1.85 }}>
                {report.executive_summary || 'Executive summary unavailable / withheld.'}
              </div>
            </Section>

            <Section title="Macro"
              note={(macro.regime_caveats || []).join(' ')}>
              <div style={{ marginBottom: 14 }}>
                <div style={{ ...mono, fontSize: 20, fontWeight: 600, color: 'var(--accent)' }}>
                  {macro.regime}
                </div>
                <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', marginTop: 2 }}>
                  {macro.regime_meaning}
                </div>
                <div style={{ ...mono, fontSize: 12, color: 'var(--ink-2)', marginTop: 4 }}>
                  {macro.regime_confidence != null &&
                    `${(macro.regime_confidence * 100).toFixed(0)}% of the classifier's weight came from an observed value`}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 14 }}>
                <Reading row={macro.vix} />
                <Reading row={macro.cpi_yoy} />
                <Reading row={macro.spread_10y2y} />
                <Reading row={macro.unemployment} />
                <Stat label="DXY" value={macro.dxy} />
                <Stat label="2Y" value={macro.treasury_2y} />
                <Stat label="10Y" value={macro.treasury_10y} />
                <Stat label="Macro score" value={macro.macro_score} />
              </div>
              <Bullets items={macro.regime_evidence} marker="→" />
            </Section>

            <Section title="Market">
              <div style={{ display: 'flex', gap: 26, marginBottom: 16, flexWrap: 'wrap' }}>
                <Stat label="Bullish" value={market.breadth?.bullish} color="var(--green)"
                  sub={`${market.breadth?.advancing_pct ?? '—'}% advancing`} />
                <Stat label="Bearish" value={market.breadth?.bearish} color="var(--red)"
                  sub={`of ${market.breadth?.tracked ?? '—'} tracked`} />
                <Stat label="VIX" value={market.volatility?.vix} color="var(--blue)" />
              </div>
              <div style={{ ...mono, fontSize: 12, color: 'var(--green)',
                marginBottom: 6 }}>Top bullish</div>
              <MoversTable rows={market.top_bullish} positive />
              <div style={{ ...mono, fontSize: 12, color: 'var(--red)',
                margin: '16px 0 6px' }}>Top bearish</div>
              <MoversTable rows={market.top_bearish} />
            </Section>

            <Section title="News" note={news.quality_note || news.note}>
              {!(news.items || []).length && (
                <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)' }}>
                  No developments were captured in the window. That is an absence
                  of observation, not an absence of news.
                </div>
              )}
              {(news.items || []).map((n, i) => (
                <div key={i} style={{ padding: '5px 0', borderBottom: '1px solid var(--inset)' }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                    <span style={{
                      ...mono, fontSize: 11, fontWeight: 600, 
                      padding: '0 4px', borderRadius: 8,
                      color: n.evidence_state === 'VERIFIED' ? 'var(--green)' : 'var(--ink-3)',
                      border: `1px solid ${n.evidence_state === 'VERIFIED' ? 'var(--green)' : 'var(--line)'}`,
                    }}>{n.evidence_state}</span>
                    <a href={n.url} target="_blank" rel="noreferrer" style={{
                      ...mono, fontSize: 13, color: 'var(--ink)', textDecoration: 'none', flex: 1,
                    }}>{n.headline}</a>
                  </div>
                  <div style={{ ...mono, fontSize: 11, color: 'var(--ink-3)', marginTop: 1 }}>
                    {String(n.source_tier || 'source tier unavailable').replace(/_/g, ' ')} · {n.source}
                    {n.corroboration > 1 && ` · corroborated ×${n.corroboration}`}
                  </div>
                </div>
              ))}
            </Section>

            <Section title="ARIA's assessment">
              <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)',
                marginBottom: 5 }}>What matters</div>
              <Bullets items={assess.what_matters} />
              <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)',
                margin: '14px 0 5px' }}>What changed</div>
              <Bullets items={assess.what_changed} />
              <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)',
                margin: '14px 0 5px' }}>What ARIA is watching</div>
              <Bullets items={assess.watching} />
              <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)',
                margin: '14px 0 5px' }}>What would invalidate this view</div>
              <Bullets items={assess.what_would_invalidate} marker="⚠" />
            </Section>

            <Section title="Track record" note={record.limitation}>
              {/* The headline carries the sample size AND the interval, so a
                  hit rate can never be read as a measured edge. On this data
                  it says "indistinguishable from chance", which is the honest
                  answer and must stay visible rather than being reduced to a
                  percentage. */}
              {record.calibration?.headline && (
                <div style={{
                  ...mono, fontSize: 13, color: 'var(--yellow)', lineHeight: 1.7,
                  marginBottom: 12, padding: '8px 10px',
                  border: '1px solid color-mix(in srgb, var(--yellow) 25%, transparent)', borderRadius: 8,
                }}>
                  {record.calibration.headline}
                </div>
              )}
              {record.calibration?.error && (
                <div style={{ ...mono, fontSize: 12, color: 'var(--red)', marginBottom: 10 }}>
                  Calibration unavailable: {record.calibration.error}
                </div>
              )}
              <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap', marginBottom: 14 }}>
                <Stat label="Resolved" value={record.stats?.resolved} />
                <Stat label="Pending" value={record.stats?.pending} />
                <Stat label="Hit rate"
                  value={record.stats?.hit_rate != null
                    ? `${(record.stats.hit_rate * 100).toFixed(1)}%` : '—'} />
              </div>
              {(record.recent_resolved || []).map((r, i) => (
                <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'baseline',
                  padding: '3px 0', borderBottom: '1px solid var(--inset)' }}>
                  <span style={{ ...mono, fontSize: 12, fontWeight: 600, color: 'var(--ink)', minWidth: 56 }}>
                    {r.subject}
                  </span>
                  <span style={{ ...mono, fontSize: 11, color: 'var(--ink-3)', minWidth: 70 }}>
                    {r.provider_symbol}
                  </span>
                  <span style={{ ...mono, fontSize: 12, color: 'var(--ink-2)', flex: 1 }}>
                    {r.claim}
                  </span>
                  <span style={{
                    ...mono, fontSize: 12, fontWeight: 600,
                    color: r.correct === 1 ? 'var(--green)'
                      : r.correct === 0 ? 'var(--red)' : 'var(--ink-3)',
                  }}>
                    {r.correct === 1 ? 'Right' : r.correct === 0 ? 'Wrong' : '—'}
                  </span>
                </div>
              ))}
            </Section>

            <Section title="Risks / blind spots">
              <Bullets items={report.blind_spots} colour="var(--yellow)" marker="△" />
            </Section>
          </>
        )}
      </div>

      {/* ── the archive rail ── */}
      <div>
        <div style={{ ...mono, fontSize: 12, fontWeight: 600, 
          color: 'var(--ink-3)', marginBottom: 9 }}>
          Report history
        </div>
        <button onClick={() => setDate(null)} style={{
          ...mono, fontSize: 12, width: '100%', textAlign: 'left', padding: '6px 8px',
          marginBottom: 4, borderRadius: 8, cursor: 'pointer',
          background: date === null ? 'color-mix(in srgb, var(--accent) 10%, transparent)' : 'transparent',
          border: `1px solid ${date === null ? 'var(--accent)' : 'var(--line)'}`,
          color: date === null ? 'var(--ink)' : 'var(--ink-2)',
        }}>Today</button>
        {reports.map(r => (
          <button key={r.date} onClick={() => setDate(r.date)} style={{
            ...mono, fontSize: 12, width: '100%', textAlign: 'left', padding: '6px 8px',
            marginBottom: 4, borderRadius: 8, cursor: 'pointer',
            background: date === r.date ? 'color-mix(in srgb, var(--accent) 10%, transparent)' : 'transparent',
            border: `1px solid ${date === r.date ? 'var(--accent)' : 'var(--line)'}`,
            color: date === r.date ? 'var(--ink)' : 'var(--ink-2)',
          }}>
            <div style={{ fontWeight: 600 }}>{r.date}</div>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 1 }}>
              {r.market_regime || '—'}
              {r.superseded && ' · superseded'}
            </div>
          </button>
        ))}
        {!reports.length && (
          <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.6 }}>
            No reports stored yet.
          </div>
        )}
        <div style={{ ...mono, fontSize: 11, color: 'var(--ink-3)', marginTop: 12,
          lineHeight: 1.6 }}>
          One report per day, kept forever. A report is never overwritten — a
          regenerated day archives the previous version beside it.
        </div>
      </div>
    </div>
  )
}
