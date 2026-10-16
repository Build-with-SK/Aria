// Keep the exact contract out of generic equity/universe resolution.
export const isExactGoldContract = symbol => ['GCZ6', 'GCZ26', 'GCZ2026', 'GCZ26.CMX'].includes(String(symbol || '').toUpperCase())

// Compact evidence for the existing owner consultation API's query parameters.
// Numeric values, no aggregate directional hypothesis or model confidence.
export function technicalEvidence(symbol, rows) {
  const values = group => Object.fromEntries(Object.entries(group || {}).map(([name, v]) => [name, [Number(v.value.toFixed(4)), ...[v.signal_line, v.plus_di, v.minus_di].filter(x => Number.isFinite(x)).map(x => Number(x.toFixed(4)))]]))
  return JSON.stringify({ symbol, evidence_type: 'Observed closed-candle prices and calculated indicators; no calibrated probability',
    timeframes: Object.fromEntries(Object.entries(rows).map(([tf, row]) => [tf, {
      instrument: row.instrument?.provider_symbol, exchange: row.provenance?.exchange || row.instrument?.exchange,
      expiration: row.instrument?.expiration, error: row.error, as_of: row.data_as_of,
      stale: row.data_stale, quality: row.data_quality, bars: row.evidence?.bars,
      provider: row.provenance?.provider, alignment: row.provenance?.alignment,
      feed_checks: row.verification && { identity: row.verification.identity, ohlc: row.verification.ohlc,
        warmup: row.evidence?.warmup_complete, independent_prices: row.verification.independent_price_validation },
      calendar: row.calendar_status, adjustment: row.provenance?.adjustment,
      excluded_prefix: row.history_prefix_excluded || row.provenance?.aggregation_source_validation?.history_prefix_excluded || row.provenance?.hourly_source_validation?.history_prefix_excluded || undefined,
      reconstructed: row.provenance?.reconstructed, incomplete_days: row.provenance?.incomplete_days_excluded,
      gap_count: row.gap_count, observed_close: row.price,
      indicators: values(row.indicator_values), moving_averages: values(row.moving_average_values),
      atr: row.volatility?.atr14, structure: row.confirmation?.structure,
    }])) })
}
