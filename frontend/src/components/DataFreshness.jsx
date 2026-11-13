import React from 'react'
import { useDataFreshness } from '../hooks/useApi'
import { SectionHeader } from './UI'
import { DataUnavailable } from './DataState'

const mono = { fontFamily: 'var(--font)' }
const cell = { padding: '8px 10px', textAlign: 'left', verticalAlign: 'top', borderBottom: '1px solid var(--line)' }
const text = value => typeof value === 'string' && value ? value : 'unavailable'
const hours = value => {
  if (!Number.isFinite(value)) return 'unavailable'
  if (value < 0) return `clock ahead by ${Math.abs(value).toFixed(1)}h`
  return value < 1 ? `${Math.round(value * 60)}m` : value < 48 ? `${value.toFixed(1)}h` : `${(value / 24).toFixed(1)}d`
}
const statusColor = state => state === 'FRESH' || state === 'COMPLETED' ? 'var(--green)'
  : state === 'STALE' || state === 'DID_NOT_FINISH' ? 'var(--red)' : 'var(--yellow)'

export default function DataFreshness() {
  const { data, loading, error } = useDataFreshness()
  const rows = Array.isArray(data?.datasets) ? data.datasets.filter(row => row && typeof row === 'object') : []
  const run = data?.pipeline_run
  const state = ['COMPLETED', 'IN_PROGRESS', 'DID_NOT_FINISH', 'UNKNOWN'].includes(run?.state) ? run.state : 'UNKNOWN'

  return <>
    <SectionHeader>Data freshness · per dataset</SectionHeader>
    {loading && !data ? <div role="status" style={{ ...mono, color: 'var(--ink-3)', padding: 16 }}>Checking dataset freshness…</div>
      : error || !Array.isArray(data?.datasets) ? <DataUnavailable name="Dataset freshness" error={error} />
      : <div className="bb-card" style={{ marginBottom: 14 }}>
        <div style={{ ...mono, display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 12, marginBottom: 8 }}>
          <span>Last pipeline run</span>
          <strong style={{ color: statusColor(state) }}>{state.replaceAll('_', ' ')}</strong>
          <span style={{ color: 'var(--ink-2)' }}>step: {text(run?.step)}</span>
        </div>
        {run?.why && <div style={{ ...mono, fontSize: 12, color: 'var(--yellow)', marginBottom: 8 }}>{text(run.why)}</div>}
        <div style={{ ...mono, fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.7, marginBottom: 10 }}>
          Snapshot: {text(data.as_of)} · started: {text(run?.started_at)} · finished: {text(run?.finished_at)}.
          {' '}The run marker covers the pipeline as a whole; each dataset has its own age below.
        </div>
        {!rows.length ? <DataUnavailable name="Dataset rows" error="No dataset observations returned." />
          : <div style={{ overflowX: 'auto' }}>
            <table style={{ ...mono, width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead style={{ color: 'var(--ink-3)', fontSize: 12 }}><tr>
                {['DATASET', 'STATE', 'AGE / BUDGET', 'DATED BY / OBSERVATION', 'REFRESH SOURCE'].map(label => <th key={label} scope="col" style={cell}>{label}</th>)}
              </tr></thead>
              <tbody>{rows.map((row, index) => {
                const status = ['FRESH', 'STALE', 'UNKNOWN'].includes(row.status) ? row.status : 'UNKNOWN'
                return <tr key={`${row.dataset}-${index}`}>
                  <td style={{ ...cell, color: 'var(--ink)', wordBreak: 'break-word' }}>{text(row.dataset)}</td>
                  <td style={{ ...cell, color: statusColor(status) }}>{status}
                    {row.legacy === true && <div style={{ color: 'var(--yellow)', fontSize: 12, marginTop: 4 }}>LEGACY · no active writer</div>}
                    {row.why && <div style={{ color: 'var(--ink-3)', fontSize: 12, marginTop: 4 }}>{text(row.why)}</div>}
                  </td>
                  <td style={{ ...cell, whiteSpace: 'nowrap', color: 'var(--ink-2)' }}>{hours(row.age_hours)} / {hours(row.stale_threshold_hours)}</td>
                  <td style={{ ...cell, color: 'var(--ink-2)' }}>
                    {text(row.as_of)}
                    <div style={{ color: 'var(--ink-3)', fontSize: 12, marginTop: 4 }}>
                      {row.basis === 'file_mtime' ? 'file write time · not a market observation'
                        : row.basis === 'stated_date' ? 'date stated in data' : 'date basis unavailable'}
                    </div>
                    {row.observation && <div style={{ fontSize: 12, marginTop: 4 }}>
                      {row.observation.recorded === true
                        ? `Last bars: ${text(row.observation.earliest_bar)} to ${text(row.observation.latest_bar)} · ${row.observation.rows_dated ?? '—'}/${row.observation.rows ?? '—'} rows dated`
                        : `Market observation unavailable: ${text(row.observation.why)}`}
                    </div>}
                  </td>
                  <td style={{ ...cell, color: 'var(--ink-3)', lineHeight: 1.6 }}>{text(row.writer)}</td>
                </tr>
              })}</tbody>
            </table>
          </div>}
      </div>}
  </>
}
