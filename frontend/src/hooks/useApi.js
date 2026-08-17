/**
 * useApi.js
 * Central data fetching hooks for the Trading Intelligence System API.
 * All components import from here — never fetch directly.
 */

import { useState, useEffect, useCallback } from 'react'

const BASE = '' // Vite proxy handles /api → localhost:8000

async function fetchJson(url) {
  const res = await fetch(BASE + url)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`)
  return res.json()
}

function useApiData(url, interval = null) {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      const d = await fetchJson(url)
      setData(d)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [url])

  useEffect(() => {
    load()
    if (interval) {
      const id = setInterval(load, interval)
      return () => clearInterval(id)
    }
  }, [load, interval])

  return { data, loading, error, reload: load }
}

// ── Exported hooks ────────────────────────────────────────────────────────

export const useSummary    = () => useApiData('/api/summary',   60_000) // refresh every 60s
export const useSignals    = () => useApiData('/api/signals',   120_000)
export const useSignal     = (ticker) => useApiData(ticker ? `/api/signals/${ticker}` : null)
export const useMacro      = () => useApiData('/api/macro',     300_000)
export const useFutures    = () => useApiData('/api/futures',   120_000)
export const useOptions    = () => useApiData('/api/options',   300_000)
export const useML         = () => useApiData('/api/ml',        120_000)
export const useBacktest   = () => useApiData('/api/backtest',  600_000)
export const usePortfolio  = () => useApiData('/api/portfolio', 300_000)
// What he ACTUALLY owns, from the broker. `usePortfolio` above returns
// data/portfolio_analysis.json — a fixed model universe last computed in May,
// which is why the page showed twenty-odd names he had never bought.
export const useHoldings   = () => useApiData('/api/portfolio/holdings', 60_000)
export const useRelated    = () => useApiData('/api/portfolio/related?per_holding=4', 300_000)
export const useAlerts     = () => useApiData('/api/alerts',    60_000)
export const useSentiment  = () => useApiData('/api/sentiment', 300_000)
export const useReport     = () => useApiData('/api/report',    300_000)
export const useHistory    = (ticker, days = 30) => useApiData(ticker ? `/api/history/${ticker}?days=${days}` : null)
export const useStats      = () => useApiData('/api/stats',     600_000)
export const useHealth     = () => useApiData('/health',        30_000)

export async function triggerRun(noSentiment = true) {
  const res = await fetch(`/api/run?no_sentiment=${noSentiment}`, { method: 'POST' })
  return res.json()
}

// ── Quant Lab (automated researcher) ─────────────────────────────────────
export const useQuantStatus     = () => useApiData('/api/quant/status',     30_000)
export const useQuantStrategies = () => useApiData('/api/quant/strategies', 60_000)
export const useQuantPapers     = () => useApiData('/api/quant/papers',     120_000)

export async function quantRunNow() {
  const res = await fetch('/api/quant/run-now', { method: 'POST' })
  return res.json()
}

// ── NEXUS / Universe (on-demand, not polled) ─────────────────────────────
export async function searchUniverse(q, n = 8) {
  if (!q) return []
  return fetchJson(`/api/universe/search?q=${encodeURIComponent(q)}&n=${n}`)
}

export async function fetchNexusResearch(symbol) {
  return fetchJson(`/api/nexus/research/${encodeURIComponent(symbol)}`)
}
