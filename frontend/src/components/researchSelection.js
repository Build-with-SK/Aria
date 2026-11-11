// A fuzzy search hit must never change the instrument chosen in the URL.
export function exactResearchSelection(rows, symbol) {
  const wanted = String(symbol || '').trim().toUpperCase()
  const display = rows.find(row => String(row.symbol || '').toUpperCase() === wanted)
  if (display) return display
  const providers = rows.filter(row => String(row.yahoo || '').toUpperCase() === wanted)
  return providers.length === 1 ? providers[0] : null
}

export function analysisRequestPlan({ technicalOnly = false, includeTechnical = true, exactContract = false } = {}) {
  return { technical: technicalOnly || includeTechnical, engine: !technicalOnly && !exactContract }
}
