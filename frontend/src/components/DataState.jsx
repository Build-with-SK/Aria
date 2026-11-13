import React from 'react'

export const hasRecord = value => value != null && !Array.isArray(value)
  && typeof value === 'object' && Object.keys(value).length > 0

export const displayValue = value => value == null ? 'unavailable'
  : typeof value === 'object' ? JSON.stringify(value) : String(value)

/** Missing observations are not zero observations. Keep that distinction visible. */
export function DataUnavailable({ name, error }) {
  return <div role="status" className="bb-card" style={{ background: 'var(--yellow-bg)', borderColor: 'transparent',
    boxShadow: 'none', color: 'var(--yellow)', fontSize: 14, fontWeight: 550, padding: 18, marginBottom: 14, lineHeight: 1.6 }}>
    {name} unavailable / withheld. {error || 'No usable response was received.'}
  </div>
}
