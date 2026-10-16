import React from 'react'

export const hasRecord = value => value != null && !Array.isArray(value)
  && typeof value === 'object' && Object.keys(value).length > 0

export const displayValue = value => value == null ? 'unavailable'
  : typeof value === 'object' ? JSON.stringify(value) : String(value)

/** Missing observations are not zero observations. Keep that distinction visible. */
export function DataUnavailable({ name, error }) {
  return <div role="status" className="bb-card" style={{ fontFamily: 'var(--mono)',
    color: 'var(--yellow)', padding: 18, marginBottom: 14, lineHeight: 1.7 }}>
    {name} unavailable / withheld. {error || 'No usable response was received.'}
  </div>
}
